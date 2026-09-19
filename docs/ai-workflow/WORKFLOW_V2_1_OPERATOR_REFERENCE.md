# Workflow v2.1 — Operator Reference

![Workflow v2.1 command and lifecycle flow](diagrams/workflow-v2-1-lifecycle.drawio.svg)

Quick reference for manually shepherding a work item through the current
command set. Describes the workflow **as it exists today**, not as it might
be redesigned.

Authoritative sources: `.claude/commands/*.md`,
`docs/ai-workflow/MILESTONE_WORKFLOW.md`,
`docs/ai-workflow/REVIEW_PROTOCOL.md`,
`docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`, `scripts/workflow_state.py`.
Where they disagree, see [Known discrepancies](#known-discrepancies).

---

## How to read this

- **States/phases** live in `docs/ai-workflow/WORKFLOW_STATE.json` per work
  item (`work_items[<id>].phase`). Check ground truth with:
  `python3 -c "import json;print(json.load(open('docs/ai-workflow/WORKFLOW_STATE.json'))['work_items'])"`
- **Three governing versions** exist per work item, fixed at creation
  (`workflow-2.5.0` widened this from two): `"1"` (single-stage plan
  review, single-stage implementation review), `"2.1"` (two-stage plan
  review, single-stage implementation review; current default in
  `WORKFLOW_CONFIG.json` for a repository that has not activated `"2.2"`),
  and `"2.2"` (two-stage plan review — identical mechanism to `"2.1"`'s —
  **plus** two-stage implementation review, `D-Implementation-Review-Stages`).
  A `"2.1"`/`"2.2"` item's two-stage plan review is the part most people get
  wrong — see below. A `"2.2"` item's two-stage *implementation* review
  mirrors it at the technical-approval gate — see the `/review-implementation`,
  `/record-manual-implementation-review`, and `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`/
  `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` sections below.
- **User-only commands** (`/approve-review`, `/accept-milestone`) carry
  `disable-model-invocation: true`.
  Claude cannot run them, and they refuse to write anything unless *your
  current-turn message* contains literal confirmation text naming the exact
  work item and stage. They also do not appear in Claude's skill list.
- **Every command stops.** None of them chain into the next command, even
  when the next step is obvious.

## Typical workflow (happy path, a `"2.1"` item)

```
/milestone-plan                     → AWAITING_LOCAL_PLAN_REVIEW
/review-plan            (APPROVE)   → AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW
  you: upload bundle → paste REVIEW_FEEDBACK.md
/record-manual-plan-review (APPROVE)→ AWAITING_PLAN_APPROVAL      ★ gate
/approve-review plan     (you)      → IMPLEMENTING
/milestone-implement     (× N, one checkpoint each)
                                    → AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW  ★ gate
  you: external review → paste REVIEW_FEEDBACK.md
       (APPROVE, nothing left to apply — go straight to the approval)
/approve-review implementation (you)→ AWAITING_FUNCTIONAL_REVIEW   ★ gate
/prepare-functional-review
  you: manual testing → FUNCTIONAL_REVIEW.md (only if findings)
/apply-functional-review            → back to AWAITING_FUNCTIONAL_REVIEW
/accept-milestone        (you)      → MILESTONE_COMPLETE
```

At either plan-review verdict, `REVISE` diverts through
`/apply-plan-review`, which always returns the item to
`AWAITING_LOCAL_PLAN_REVIEW` — never straight back to manual review.

At implementation review, `REVISE`/`BLOCK` diverts through
`/apply-implementation-review`, which enters `APPLYING_REVIEW_FEEDBACK`,
fixes, and republishes a `post-fix` bundle back at
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`. **`/apply-implementation-review`
is the `REVISE` path, not the `APPROVE` path.** Running it on a clean
`APPROVE` with nothing to apply is actively harmful: step 7 republishes
the bundle regardless (as a `same_content` round), which moves
`bundle_id`, so the `APPROVE` you already have no longer binds — the
next `/approve-review implementation` refuses with
`FeedbackBundleMismatchError` and, if forced past it, records
`USER_OVERRIDE` instead of `EXTERNAL_APPROVE`. Reproduced directly; see
ledger `I11`.

`AWAITING_TECHNICAL_APPROVAL` is deliberately absent from the sequence
above. Nothing writes it — see [Phases](#phases). The item sits at
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` until
`/approve-review implementation` moves it straight to
`AWAITING_FUNCTIONAL_REVIEW`.

Two more commands are optional and non-gating, so they don't appear in
the happy path above: `/review-implementation` (at
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) and `/review-functional` (at
`AWAITING_FUNCTIONAL_REVIEW`) each give a repository-local second opinion
before handing the stage to its real gate — see their own sections below.

## The two-stage plan review (the part that causes confusion)

A `"2.1"` work item has **two** distinct plan reviews, in a fixed order.
They are not interchangeable and neither one approves the plan.

| Step | Who | Command | Records |
|---|---|---|---|
| 1. Local model review | Claude, ideally a fresh session | `/review-plan` | Writes `REVIEW_FEEDBACK.md` itself; on `APPROVE` records the `LOCAL_MODEL_PLAN_REVIEW` ledger stage |
| 2. Manual external review | **You** — upload the bundle to an external reviewer, paste its verdict into `REVIEW_FEEDBACK.md` | `/record-manual-plan-review` | Ingests and binds the verdict you already pasted; on `APPROVE` records the `MANUAL_EXTERNAL_PLAN_REVIEW` ledger stage |
| 3. Approval | **You** | `/approve-review plan` | The only approval gate; requires **both** ledger stages recorded against the *current* `review_content_id` |

Key points:

- `/review-plan` **performs and writes** a review. It is the reviewer.
- `/record-manual-plan-review` **performs no review at all**. It never reads
  the plan for quality and never edits it — it only ingests and binds a
  verdict a human reviewer already produced and already pasted into
  `REVIEW_FEEDBACK.md` in the same turn. Nothing in the workflow can act on
  an external review until this command has bound it.
- Then, by verdict:
  - **`REVISE`** (either stage) → `/apply-plan-review`.
  - **`APPROVE`** at stage 1 → the manual external review is next (upload,
    paste, `/record-manual-plan-review`). `APPROVE` at stage 2 →
    `/approve-review plan`.
  - **`BLOCK`** (either stage) → nothing is recorded, the phase does not
    change. Resolve it with the user explicitly, then re-run.
- The two ledger stage names are `LOCAL_MODEL_PLAN_REVIEW` and
  `MANUAL_EXTERNAL_PLAN_REVIEW` — `SCREAMING_SNAKE_CASE` is canonical
  everywhere: in `plan_review_stages` keys, in what `/review-plan` writes,
  and in what `/record-manual-plan-review` expects. The lowercase
  `local_model_plan_review`/`manual_external_plan_review` spellings are
  **legacy compatibility values only**, normalized on read
  (`normalize_plan_review_stages`/`_normalize_plan_review_stage_key` on every read, `migrate_plan_review_stage_keys` once);
  never write them.
- The manual-stage feedback file must declare
  `Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW` (the legacy lowercase
  spelling is still accepted, nothing else is), and its
  `review_content_id` must match the current recomputed value, or ingestion
  is refused. A mismatched `bundle_id` only warns.
- Any edit to a protected plan doc changes `review_content_id`, which makes
  **both** ledger stages read as absent immediately. There is no clear step;
  it is by recomputation.

A `"1"` item skips all of this: it goes `AWAITING_EXTERNAL_PLAN_REVIEW` →
`/apply-plan-review` → `/approve-review plan`. `/review-plan` and
`/record-manual-plan-review` both refuse cleanly against a `"1"` item — and
because `record_manual_plan_review` is the *only* writer of
`AWAITING_PLAN_APPROVAL`, a `"1"` item never occupies that phase: it stays
at `AWAITING_EXTERNAL_PLAN_REVIEW` until `/approve-review plan` moves it to
`IMPLEMENTING`. For a `"1"` item `AWAITING_PLAN_APPROVAL` is a gate name,
exactly like `AWAITING_TECHNICAL_APPROVAL` is for both versions.

## Which command do I run next?

| Current phase / situation | Run this |
|---|---|
| `PLANNING`, or starting a milestone | `/milestone-plan` |
| `AWAITING_LOCAL_PLAN_REVIEW` (`"2.1"`) | `/review-plan` — ideally a fresh session |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | Upload the named bundle, paste the verdict into `REVIEW_FEEDBACK.md`, then `/record-manual-plan-review` |
| `AWAITING_EXTERNAL_PLAN_REVIEW` (`"1"` only) | Paste external feedback, then `/apply-plan-review` |
| Any plan verdict was `REVISE` | `/apply-plan-review` → returns a `"2.1"` item to `AWAITING_LOCAL_PLAN_REVIEW`; a `"1"` item stays at `AWAITING_EXTERNAL_PLAN_REVIEW` |
| Any verdict was `BLOCK` | Nothing. Resolve with the user, then re-run the same command |
| `REVISING_PLAN` | `/apply-plan-review` |
| `AWAITING_PLAN_APPROVAL` | **You**: `/approve-review plan <work-item-id>` with literal confirmation |
| `IMPLEMENTING` | `/milestone-implement` — once per checkpoint, repeat until it reports all complete |
| `SELF_REVIEWING_IMPLEMENTATION` | `/milestone-implement` again (it runs verification + generates the bundle) |
| `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, want a repository-local second opinion first | `/review-implementation` (optional) — writes the authoritative `REVIEW_FEEDBACK.md` itself |
| `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, feedback says `APPROVE` | **You**: `/approve-review implementation [work-item-id]` — do **not** route a clean `APPROVE` through `/apply-implementation-review` |
| `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, feedback says `REVISE`/`BLOCK` | Paste external feedback (unless `/review-implementation` already wrote it), then `/apply-implementation-review` |
| Same, but `/approve-review implementation` fails on provenance after an unrelated doc/scripts commit | `/recover-implementation-provenance` |
| `APPLYING_REVIEW_FEEDBACK` | `/apply-implementation-review` is mid-run; finishing its step 7 republishes the bundle and returns the item to `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` |
| Post-fix bundle published, blocking findings remain | Another external review round → `/apply-implementation-review` |
| `AWAITING_FUNCTIONAL_REVIEW`, no checklist yet | `/prepare-functional-review` |
| `AWAITING_FUNCTIONAL_REVIEW`, want a second opinion on checklist completeness first | `/review-functional` (optional) — report-only, writes nothing |
| Functional testing produced findings | `/apply-functional-review` |
| Functional testing clean, **all** own checkpoints `COMPLETE` | **You**: `/accept-milestone` |
| Functional testing clean, a checkpoint still outstanding | `/milestone-implement` — finish it, then return to this gate |
| Review needed for work outside the gates | `/prepare-review` |
| Driving the `workflow-v2-1-core` item | `/bootstrap-workflow-v2` — its only driver, but that item is `MILESTONE_COMPLETE`, so every path through it now fails closed (ledger `O16`) |

There is only one acceptance command. `/accept-milestone` refuses while the
item's own registry has any checkpoint that is not `COMPLETE`, and its
refusal names the outstanding checkpoint plus the three supported ways
forward: finish the checkpoint with `/milestone-implement` if it is still
in scope; `/apply-functional-review`'s bounded branch for a same-scope
functional fix; its broad branch — a `<parent-id>-remediation-<n>` child
work item — for new or wider scope. Nothing records functional acceptance
of a partial round: `/accept-scoped-remediation` did, and was retired as an
unreachable dead contract (ledger `I10`).

---

## Command reference

`.claude/commands/` holds 16 files today, and all 16 are sectioned below
— including `/review-implementation` and `/review-functional`
(`workflow-v2-3`) and `/request-plan-amendment` (`workflow-2.4.0`),
previously undocumented here. `[work-item-id]` defaults
to `active_work_item_id` where accepted; naming it explicitly is what lets
you drive a work item that is **not** the active one, which is exactly how
a remediation child is run (see [Remediation
children](#remediation-children-a-child-work-items-own-cycle)).

What is actually guaranteed about that inventory, precisely:
`workflow_integration_test.py`'s
`test_the_operator_reference_command_count_matches_reality` derives the
set of `### /<name>` sections in this document and the set of
`.claude/commands/*.md` stems on disk and asserts the two are **equal** —
that part is derived, so a command added or removed without a section here
fails. The number 16 itself is *not* derived: it is a hardcoded tripwire
in that same test (`assertEqual(len(on_disk), 16)`) whose job is to make a
change in the roster size a deliberate, reviewed edit. So the set equality
is mechanical; the count in this paragraph and in the test is a
hand-maintained pair that must be updated together. If they disagree, the
test fails and is authoritative about which one moved.

### `/milestone-plan [work-item-id] [base-sha]`
- **When**: starting or resuming a milestone's plan, or planning a named
  existing work item — including a remediation child, which is never the
  active item.
- **Arguments**: none → derive the milestone from
  `docs/ACTIVE_MILESTONE.md`/`docs/ROADMAP.md`. One argument → a
  `work_items` key selects that work item, anything else is a base SHA
  (so the old `/milestone-plan <base-sha>` form is unchanged). Two →
  `<work-item-id> <base-sha>`. Nothing is guessed; the id selects an
  existing entry only.
- **Expects**: `PLANNING`, or an existing non-terminal work item to resume.
- **Does**: identifies the next incomplete milestone, writes the execution
  plan, generates the registry/mapping/artifacts declarations, self-reviews,
  and builds the plan bundle.
- **Writes**: plan doc, `registry/<id>-registry.json`,
  `requirements/<id>-mapping.json`, `registry/<id>-artifacts.json`,
  `WORKFLOW_STATE.json`, plan bundle. No commits.
- **Next**: `AWAITING_LOCAL_PLAN_REVIEW` (`"2.1"`) or
  `AWAITING_EXTERNAL_PLAN_REVIEW` (`"1"`).
- **Refuses**: reuse of a terminal or previously-used `work_item_id`; a
  checkpoint id reintroduced after retirement; a `REJECTED` bundle marker;
  an argument that resolves as neither a `work_items` key nor a commit; a
  `<base-sha>` conflicting with a selected entry's own stored
  `base_commit`.
- **Never**: repoints `active_work_item_id` away from another live item.
  `route_work_item` claims the pointer only when it is free or already
  this item's.

### `/review-plan [work-item-id]`
- **When**: immediately after `/milestone-plan` or `/apply-plan-review`, on a
  `"2.1"` item. Strongly recommended in a **fresh session**.
- **Expects**: `phase == AWAITING_LOCAL_PLAN_REVIEW`, `"2.1"`/`"2.2"` only (widened workflow-2.5.0 from a bare `"2.1"` check -- `TWO_STAGE_PLAN_REVIEW_VERSIONS` membership).
- **Does**: independently re-verifies the plan against the repository —
  including every finding the plan claims as addressed — and decides
  `APPROVE`/`REVISE`/`BLOCK`.
- **Writes**: `REVIEW_FEEDBACK.md` (with `Reviewer role:
  LOCAL_MODEL_PLAN_REVIEW` — the canonical spelling, never a model name)
  and, on `APPROVE` only, the ledger stage + phase in
  `WORKFLOW_STATE.json`. Never the plan, registry, mapping, or bundle.
- **Next**: `APPROVE` → `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`; `REVISE` →
  `REVISING_PLAN`; `BLOCK` → stays put.
- **Refuses**: a `"1"` item; wrong phase (including a re-run after this
  round already completed); a stale bundle vs. `MANIFEST.md`; a worktree/HEAD
  mismatch; a `REJECTED` bundle.

### `/record-manual-plan-review [work-item-id]`
- **When**: after you have pasted the external reviewer's verdict into
  `REVIEW_FEEDBACK.md`, in the same turn.
- **Expects**: `phase == AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `"2.1"`/`"2.2"` only (widened workflow-2.5.0 from a bare `"2.1"` check -- `TWO_STAGE_PLAN_REVIEW_VERSIONS` membership).
- **Does**: mechanically ingests and binds that verdict. Evaluates nothing.
- **Writes**: `WORKFLOW_STATE.json` only (ledger stage on `APPROVE`, phase on
  `APPROVE`/`REVISE`). Never the feedback file, plan, registry, or bundle.
- **Next**: `APPROVE` → `AWAITING_PLAN_APPROVAL`; `REVISE` → `REVISING_PLAN`;
  `BLOCK` → stays put.
- **Refuses**: a `Reviewer role:` that normalizes to anything other than
  `MANUAL_EXTERNAL_PLAN_REVIEW` (the legacy lowercase
  `manual_external_plan_review` still normalizes to it and is accepted;
  a local-role, unlabeled or otherwise-named file is not); a
  `review_content_id` that does not match
  the current recomputed value (hard); a missing current local `APPROVE`; a
  duplicate ingestion for the same `review_content_id`. A mismatched
  `bundle_id` **warns only**. Not an approval gate.

### `/apply-plan-review [work-item-id]`
- **When**: a plan review returned `REVISE` (either stage, or a `"1"` item's
  single stage).
- **Expects**: `REVIEW_FEEDBACK.md` present and binding-field-valid; enters
  `REVISING_PLAN`.
- **Does**: validates every finding against the repository, applies accepted
  ones, records evidence-based rejections inline in the plan.
- **Writes**: plan doc, bundle `PLAN.md`, registry/mapping when the plan
  revision advances, `WORKFLOW_STATE.json`, regenerated plan bundle. No
  product code.
- **Next**: `"2.1"` → **always** `AWAITING_LOCAL_PLAN_REVIEW`, regardless of
  edit size (`transition_to_awaiting_local_plan_review`, step 7'). `"1"` →
  the phase is left at `AWAITING_EXTERNAL_PLAN_REVIEW`; step 7 *reports*
  `AWAITING_PLAN_APPROVAL` as the next gate but writes no phase, since
  nothing writes that phase for a `"1"` item.
- **Refuses**: missing feedback; feedback whose bundle/base/work-item binding
  fields do not match; a `REJECTED` bundle. Never self-declares the plan
  ready on a `"2.1"` item.

### `/approve-review <plan|implementation> [work-item-id]` — user-only
- **When**: `plan` at `AWAITING_PLAN_APPROVAL` for a `"2.1"`/`"2.2"` item (a
  real persisted phase) or at `AWAITING_EXTERNAL_PLAN_REVIEW` for a `"1"`
  one (where the same gate has no phase of its own);
  `implementation` at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` with the
  technical-approval gate **computed** reachable
  (`technical_approval_gate_reachable`) — `AWAITING_TECHNICAL_APPROVAL` is
  a gate name, never a phase the item is parked in.
- **Expects**: the matching gate to be reachable. For `plan` on a
  `"2.1"`/`"2.2"` item, both plan-review ledger stages must be current
  (`TWO_STAGE_PLAN_REVIEW_VERSIONS` covers both). For `implementation`: the
  latest round `APPROVE`/`REVISE` (never `BLOCK`, and never a durably
  pinned `BLOCK`), no dirty protected path, and HEAD matching
  `reviewed_implementation_head` through the provenance interval --
  **`workflow-2.5.0`**: for a `"2.2"` item specifically, also both
  implementation-review ledger stages (`LOCAL_MODEL_IMPLEMENTATION_REVIEW`/
  `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`) current against the same
  `review_content_id`, mirroring the plan-stage ledger check exactly.
- **Does**: recomputes `bundle_id`/`review_content_id` fresh, resolves the
  approval basis (`EXTERNAL_APPROVE` when the current feedback says
  `APPROVE` and its bundle id matches exactly, otherwise `USER_OVERRIDE`),
  and creates the approval commit.
- **Writes**: `plan_approval`/`technical_approval` in `WORKFLOW_STATE.json`
  plus one approval commit — plan stage: plan + registry + mapping + state
  (+ artifacts declaration when pending and fresh), via a crash-resumable
  journal; implementation stage: metadata-only, zero production/test changes.
- **Next**: `plan` → `IMPLEMENTING`; `implementation` →
  `AWAITING_FUNCTIONAL_REVIEW`.
- **Refuses**: no literal current-turn confirmation naming the exact work
  item and stage; a `BLOCK` status (and, at the implementation stage, a
  durably pinned `BLOCK` even if the feedback file was later edited); a
  dirty protected path; a stale worktree/HEAD; a failed provenance interval;
  an already-open approval journal (report evidence and stop — takeover needs
  a separate literal authorization).

### `/milestone-implement [work-item-id]`
- **When**: `IMPLEMENTING`, once per checkpoint.
- **Expects**: `plan_approval.status == CURRENT`, the approval commit an
  ancestor of HEAD, and the recomputed plan-stage `review_content_id` still
  matching what was approved.
- **Does**: selects and implements exactly **one** checkpoint, runs the
  narrowest relevant check, commits it with
  `Workflow-Checkpoint:`/`Workflow-Work-Item:` trailers, then stops. On the
  invocation that first sees every checkpoint `COMPLETE`, it instead
  self-reviews, runs the full suite, and generates the implementation bundle.
- **Writes**: source/tests, `docs/ACTIVE_MILESTONE.md` (or the requirements
  ledger for a process item), `WORKFLOW_STATE.json`, checkpoint commits, a
  bundle-generation-record commit, bundle files.
- **Next**: `IMPLEMENTING` (more checkpoints) →
  `SELF_REVIEWING_IMPLEMENTATION` → `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`.
- **Refuses**: an unreachable/stale plan approval; a checkpoint claimed by
  another worktree; a blocked checkpoint with unmet dependencies. Never loops
  across checkpoints, and never crosses the last-checkpoint→wrap-up boundary
  in one invocation.

### `/request-plan-amendment [work-item-id]` — user-only
- **When**: `IMPLEMENTING` or `SELF_REVIEWING_IMPLEMENTATION` — the only two
  phases this release supports (`workflow-2.4.0`, `D-Plan-Amendment-1`).
- **Expects**: no checkpoint `IN_PROGRESS` and no outstanding checkpoint
  claim; no open `/approve-review plan` transaction; the current
  `plan_approval`'s own approval commit still discoverable and an ancestor
  of `HEAD`; every checkpoint id in the work item's own current registry
  of the shape `CP<digits>[A-Z]?` -- `CP` followed by one or more digits,
  optionally followed by exactly one uppercase letter (widened,
  workflow-2.5.1, `D-Checkpoint-Id-Anchor-Grammar-Widening`, IMPL2-R1) --
  the only shape `validate_post_anchor_coverage` can ever match.
- **Does**: supersedes the current plan approval and moves the item to
  `AMENDING_PLAN` in one transaction — same authority shape as
  `/approve-review`/`/accept-milestone` (`disable-model-invocation: true`
  plus a literal confirmation naming the work item and the word
  `amendment`, plus a required, non-empty `reason` recorded verbatim). It
  never reads, writes, or compares against `USER_OVERRIDE`.
- **Writes**: `plan_approval.status = "SUPERSEDED"`; one append-only
  `amendment_history` entry (bounded, content-addressed — a single
  `pre_amendment_approval_commit` SHA, never a stored copy of the plan or
  registry documents); `amendment_base_commit`; `WORKFLOW_STATE.json`,
  committed alone.
- **Next**: `AMENDING_PLAN` — the very next `/milestone-plan [work-item-id]`
  resumes it through that command's own existing dual-mode branch, exactly
  like any other non-terminal entry. No new plan-review machinery: the
  full two-stage review protocol and `/approve-review plan` run completely
  unmodified. Checkpoint reconciliation happens once, folded into that
  eventual `/approve-review plan`'s own `apply_plan_approval` computation
  (`D-Plan-Amendment-4`), never here. The amended plan document drafted in
  response must delimit every registry checkpoint id with a
  `<!-- CPn -->`/`<!-- /CPn -->` anchor pair (`n` is the same widened
  `CP<digits>[A-Z]?` shape named above), or `/approve-review plan`
  step 4c's `validate_post_anchor_coverage` refuses approval naming the
  first uncovered id.
- **Refuses**: the wrong phase
  (`WrongPhaseForAmendmentRequestError`); a checkpoint still `IN_PROGRESS`
  or an outstanding checkpoint claim (`AmendmentCheckpointActiveError`,
  XMODEL-R4-B1, checked *before* superseding anything and *authoritatively*
  inside `request_plan_amendment` itself, not only this command's own
  preflight read -- closing the race in which a checkpoint claim is
  published to the filesystem claims directory, step 1d, before
  `WORKFLOW_STATE.json` shows anything IN_PROGRESS; the independent second
  half, refusing a checkpoint's own `IN_PROGRESS` publication once the item
  has left `IMPLEMENTING`, is `transition_checkpoint_in_progress`'s own
  `IllegalCheckpointStartPhaseError`); a registry row with no `id` key
  (`AmendmentRegistryMissingIdError`, IMPL4-O2); an unreachable approval
  commit (`AmendmentApprovalCommitUnreachableError`, checked *before*
  superseding anything); a registry checkpoint id not of the shape
  `CP<digits>[A-Z]?` (`AmendmentCheckpointIdShapeError`, also checked
  *before* superseding anything, naming every offending id -- IMPL2-R1);
  an open plan-approval transaction; no literal confirmation/`reason` this
  turn.

### `/review-implementation [work-item-id]` — review command
- **When**: optional, repeatable, repository-local second opinion while a
  bundle sits at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` — usable before
  or instead of waiting on the real external round.
- **Expects**: `phase == AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` exactly
  (refuses cleanly otherwise, naming the actual phase); a generated,
  non-`REJECTED` implementation/post-fix bundle. A bundle generated
  without the optional work-item-id argument carries no implementation-
  stage `MANIFEST.md` at all and fails closed
  (`MissingRequiredBundleFileError`) rather than silently reviewing it.
- **Does**: implements a model-independent review role, not a specific
  model. Reproduces `TEST_RESULTS.md`'s claims by rerunning the tests
  itself, reads `DIFF.patch`/`files/` directly rather than trusting the
  summary, checks the implementation against the approved plan
  (`PLAN.md`/`plan_path`), and composes a `REVIEW_FEEDBACK.md`-shaped
  verdict (`Status: APPROVE | REVISE | BLOCK`, the same structure and
  binding fields an external round's feedback carries) — exactly as
  thorough as `/apply-implementation-review`'s own step 2 validation.
- **Writes**: `<feedback_dir>/REVIEW_FEEDBACK.md` only — and, since
  `workflow-v2-3-followups`, this **is** the authoritative round for
  `/apply-implementation-review`/`/approve-review implementation` to act
  on the moment it lands, with no separate operator installation step
  (overwrites whatever same-work-item feedback already sits there). Never
  `docs/ai-workflow/WORKFLOW_STATE.json`; never approves; never applies
  findings; never advances `phase`; never edits source/test/plan content.
- **Next**: nothing automatic — the operator still runs
  `/apply-implementation-review` (on `REVISE`/`BLOCK`) or proceeds to
  `/approve-review implementation` (on `APPROVE`), exactly as after a
  human-authored round.
- **Refuses**: a `REJECTED` bundle marker (checked twice — once before
  composing, once again immediately before the write, so a withdrawal
  mid-review is still caught); a stale/absent bundle
  (`MissingRequiredBundleFileError`); a worktree/HEAD mismatch
  (`assert_local_generation_matches`, the same repository-local
  discipline `/approve-review`/`/review-plan` already apply); feedback
  already present for a different work item
  (`FeedbackOwnedByOtherWorkItemError` — the composed report still prints
  in full, only the write is refused, naming both work-item ids); any
  phase other than `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`.
- **`workflow-2.5.0`, `"2.2"` authoritative branch**: for a `"2.2"` item at
  `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` specifically, this command is no
  longer advisory-only — it is the authoritative
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage writer
  (`D-Implementation-Review-Stages`), mirroring `/review-plan`'s own `"2.1"`
  role. It writes the `implementation_review_stages` ledger and does
  advance `phase` on `APPROVE` (→ `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`)
  and `REVISE` (→ `APPLYING_REVIEW_FEEDBACK` directly, no
  `enter_applying_review_feedback` call). A `"2.2"` item at any *other*
  phase — most commonly its own terminal `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
  once both implementation-review stages have already approved — still
  takes the advisory branch described above, unchanged. See
  `.claude/commands/review-implementation.md`'s own "`"2.2"` authoritative
  branch" section for the full step sequence.

### `/record-manual-implementation-review [work-item-id]` (`workflow-2.5.0`, `"2.2"` only)
- **When**: after you have pasted the external reviewer's verdict into
  `REVIEW_FEEDBACK.md`, in the same turn, for a `"2.2"` item sitting at
  `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`. Mirrors
  `/record-manual-plan-review` exactly, substituted for the implementation
  stage.
- **Expects**: `governing_workflow_version == "2.2"` (this two-stage
  *implementation*-review protocol is `"2.2"`-only, unlike the plan-review
  protocol which also covers `"2.1"`); `phase ==
  AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`.
- **Does**: mechanically ingests and binds that verdict. Evaluates nothing
  — not a user-authority gate, no `disable-model-invocation`/
  `user_confirmation` requirement.
- **Writes**: `WORKFLOW_STATE.json` only (ledger stage on `APPROVE`, phase
  on `APPROVE`/`REVISE`). Never the feedback file, plan/registry/mapping,
  source, or bundle.
- **Next**: `APPROVE` → `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` (the
  existing terminal phase name, reused, not a new one); `REVISE` →
  `APPLYING_REVIEW_FEEDBACK` directly (no `enter_applying_review_feedback`
  call — that writer's own only legal source phase,
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, is wrong for this branch);
  `BLOCK` → stays put.
- **Refuses**: any `governing_workflow_version` other than `"2.2"`; a
  `Reviewer role:` other than exactly `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
  (no legacy-cased alias exists for this ledger — it is new at `"2.2"`); a
  `review_content_id` that does not match the current recomputed value
  (hard); a missing current local `LOCAL_MODEL_IMPLEMENTATION_REVIEW` `APPROVE`; a
  duplicate ingestion for the same `review_content_id`. A mismatched
  `bundle_id` **warns only**. Not an approval gate.

### `/apply-implementation-review [work-item-id]`
- **When**: external implementation-review feedback has been pasted **and
  says `REVISE` or `BLOCK`**. Not the `APPROVE` path — see the caveat
  below.
- **Expects**: `phase == AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` for a
  `"1"`/`"2.1"` item — refuses outright otherwise, naming the actual phase.
  Enters `APPLYING_REVIEW_FEEDBACK` (`enter_applying_review_feedback`, this
  state's only writer for those two versions). **`workflow-2.5.0`**: for a
  `"2.2"` item, whether this command calls `enter_applying_review_feedback`
  is phase-conditional, never version-conditional (this file's own
  dual-mode note above states the general rule). The ordinary two-stage
  `REVISE` loop arrives at `APPLYING_REVIEW_FEEDBACK` already — written
  directly by `/review-implementation`'s or
  `/record-manual-implementation-review`'s own `REVISE` branch — so step
  0's version-independent guard finds that phase already set and skips the
  call. The `"2.2"` terminal-phase escape (a late fix committed after both
  implementation-review stages already `APPROVE`d and the item reached
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) is still at that phase when
  this command runs, so the call *does* fire for it, exactly as the
  `APPLYING_REVIEW_FEEDBACK` row below states (in its `Phase -> writer`
  table, not by line number, since that table's own line numbers have
  moved in four of the last five rounds). Steps 1-8 below run
  identically regardless of which path got here.
- **Does**: reproduces and validates every Blocking/Important finding, fixes
  what is real, records evidence-based rejections, reruns narrow tests then
  the full suite, commits, and regenerates the `post-fix` bundle.
- **Writes**: source/tests, `IMPLEMENTATION_SUMMARY.md`,
  `WORKFLOW_STATE.json` (durable `BLOCK` pin, bundle-generation record),
  fix commits, one generation-record commit, bundle.
- **Next**: `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` for a `"1"`/`"2.1"`
  item — always, on both branches. Step 7's `record_bundle_generation` is
  the writer, and that is the phase it always writes for those versions.
  **`workflow-2.5.0`**: for a `"2.2"` item, that same writer's own
  version-dependent `bundle_generation_target_phase` resolver instead
  writes `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` — a `"2.2"` post-fix round
  re-enters both implementation-review stages, never going straight back to
  the terminal phase. If blocking findings remain or the fix was
  structurally significant, another local (`"2.2"`) or external
  (`"1"`/`"2.1"`) round runs from there; otherwise, once at the terminal
  phase, `/approve-review implementation` is next, which is what the
  command means when it reports "`AWAITING_TECHNICAL_APPROVAL` is the next
  state" — that is a *gate name*, not a phase the item ever occupies.
- **Caveats**: a `BLOCK` verdict is pinned durably to the bundle id, so
  editing the feedback file afterwards cannot make it override-eligible. The
  generation-record commit must land **before** bundle generation. And do
  not run this on a clean `APPROVE` with nothing to apply: step 7
  republishes the bundle unconditionally (as a `same_content` round),
  which moves `bundle_id` and invalidates the `APPROVE` you already have
  — `/approve-review implementation` then refuses with
  `FeedbackBundleMismatchError`, and forcing past it records
  `USER_OVERRIDE` rather than `EXTERNAL_APPROVE` (ledger `I11`).

### `/recover-implementation-provenance [work-item-id]`
- **When**: `/approve-review implementation` fails its provenance check only
  because a legitimate excluded-only commit (docs, `scripts/`) landed on top
  of the bundle-generation-record commit.
- **Expects**: for a `"1"`/`"2.1"` item, `phase ==
  AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` — the only legal source phase.
  **`workflow-2.5.0`**: for a `"2.2"` item, `phase` must be a member of
  `bundle_generation_recovered_role_legal_committed_phases("2.2")` — the
  three-phase set `{AWAITING_LOCAL_IMPLEMENTATION_REVIEW,
  AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW,
  AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW}`, every phase a `"2.2"` item can
  occupy between a generation-record commit `T` and technical approval.
  This command's own phase guard and that function's return value are, by
  construction, the identical set.
- **Does**: diagnoses read-only, then records the wider still-identical
  interval with one recovery commit and regenerates the bundle at the new tip.
- **Writes**: `WORKFLOW_STATE.json` (`state_revision`/`last_transition`
  only), one commit with `Workflow-Bundle-Generation-Record` +
  `Workflow-Work-Item` + `Workflow-Supersedes` trailers, regenerated bundle.
  `reviewed_implementation_head`/`implementation_revision` are untouched;
  the phase does not change.
- **Next**: unchanged — still whichever of the legal source phases above
  this invocation was run from (`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
  for `"1"`/`"2.1"`, byte-identical to before `workflow-2.5.0`).
- **Refuses**: any other phase; content that genuinely changed; no literal
  current-turn confirmation naming the work item **and** the superseded
  commit SHA. Afterwards the `bundle_id` has changed, so prior external
  feedback is stale — a fresh review is needed for an `EXTERNAL_APPROVE`.

### `/prepare-functional-review [work-item-id]`
- **When**: right after `/approve-review implementation`, which is what put
  the item at `AWAITING_FUNCTIONAL_REVIEW` and made
  `technical_approval.status` `CURRENT`.
- **Expects**: a resolvable target, and nothing else. It has **no** phase
  guard and **no** `technical_approval` precondition of its own — the
  ordinary lifecycle is what guarantees both, and running it earlier
  produces a checklist and an evidence commit for a round that has not been
  approved (the same non-guarding shape ledger `O7` records for
  `/milestone-implement`; no gate downstream is weakened, since
  `/review-functional` and `/accept-milestone` both do check the phase).
  Its own opening line names `AWAITING_FUNCTIONAL_REVIEW`, but the only
  phase it ever writes is on the `LEGACY_READY` adoption path, where
  `promote_legacy_work_item` writes exactly that phase.
- **Does**: confirms verification is current and writes a concrete manual
  test checklist (setup, data, flows, expected results, limitations).
- **Writes**: the checklist in `docs/ACTIVE_MILESTONE.md`, plus a dedicated
  content-idempotent checklist-evidence commit
  (`Workflow-Functional-Checklist:` trailer). May write
  `WORKFLOW_STATE.json` on legacy adoption.
- **Next**: hard gate — you test manually, findings go to
  `FUNCTIONAL_REVIEW.md`.
- **Caveat**: note the reported evidence commit SHA and blob — they name the
  exact committed checklist content you are testing against, which is not
  necessarily the current working-tree file. `/review-functional` reads the
  same identity back and flags a drift.

### `/review-functional [work-item-id]` — review command
- **When**: optional second opinion on the functional-review checklist's
  completeness, while `AWAITING_FUNCTIONAL_REVIEW`, before the user
  spends time on the manual pass.
- **Expects**: `phase == AWAITING_FUNCTIONAL_REVIEW` exactly; committed
  checklist-evidence for the current round
  (`discover_current_functional_checklist_evidence`) whose blob matches
  `docs/ACTIVE_MILESTONE.md`'s live working-tree content.
- **Does**: implements the same model-independent review role
  `/review-implementation` does, adapted to this stage's artifact shape —
  there is no bundle, `bundle_id`, or `MANIFEST.md` here, so it never
  calls `assert_local_generation_matches`. Re-runs every
  automated-verification command the checklist cites and confirms the
  real output; assesses whether the checklist's manual-flow list actually
  covers the behavioral surface of the diff since `base_commit` (or the
  prior round's own evidence commit, for a continued round); flags a
  stale command, a missing setup step, an uncovered code path, or
  untestable wording.
- **Writes**: nothing. Prints the composed report directly in the turn's
  response — never `docs/ACTIVE_MILESTONE.md`,
  `<feedback_dir>/FUNCTIONAL_REVIEW.md`, or `WORKFLOW_STATE.json`. Its
  report is a checklist-completeness/evidence-reproducibility opinion,
  **not** a `Status: APPROVE | REVISE | BLOCK` verdict — there is no
  bundle or ledger stage at this gate for a verdict to gate.
- **Next**: nothing automatic. Only the user's own manual execution of
  the checklist's required flows can actually satisfy this gate — an app
  walkthrough for product/UI work, command/process checks for a process
  work item; this report is a pre-check, never a substitute. The operator
  may revise the checklist by hand from the report — always the user's
  choice, never automatic.
- **Refuses**: a `REJECTED` marker (work-item-scoped, checked once,
  immediately before the report — the same guard
  `/apply-functional-review`'s bounded-fix branch is a required consumer
  of); no committed checklist evidence yet for this round (points at
  `/prepare-functional-review`); a
  `NonFirstParentFunctionalChecklistEvidenceError` (inherited from
  `/prepare-functional-review` step 3a); a checklist edited since its own
  evidence commit (names both blobs, refuses rather than reviewing stale
  evidence); any phase other than `AWAITING_FUNCTIONAL_REVIEW`.
- **Caveat**: if `technical_approval.status` is `STALE`, this is reported
  prominently rather than silently — the checklist may predate a
  since-invalidated implementation.

### `/apply-functional-review [work-item-id]`
- **When**: `FUNCTIONAL_REVIEW.md` exists with findings.
- **Expects**: `AWAITING_FUNCTIONAL_REVIEW`, and stays there for the
  no-code-change and broad branches. The command's opening line names
  `FIXING_FUNCTIONAL_FINDINGS`, but **nothing writes that phase** — it is
  narrative vocabulary from `MILESTONE_WORKFLOW.md`'s v1 state list, not a
  persisted stop (see [Phases](#phases)). The one branch that really moves
  the item is the bounded fix: `mark_technical_approval_stale` →
  `STALE` **before the first edit**, then step 4's
  `record_bundle_generation(stage="post-fix")`, which is what writes
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`. That call is legal from
  `AWAITING_FUNCTIONAL_REVIEW` *only* because of the `STALE` marker; with
  a `CURRENT` `technical_approval` it refuses with
  `BundleGenerationRequiresStaleTechnicalApprovalError`.
- **Does**: classifies each finding (defect / usability / missing requirement
  / enhancement / expected behavior), then routes it into one of three
  branches decided **before** any edit lands:
  - *no code change* → back to `AWAITING_FUNCTIONAL_REVIEW`,
    `technical_approval` untouched;
  - *bounded fix* → marks `technical_approval` `STALE` **before** the first
    edit, fixes, commits, regenerates the `post-fix` bundle, and stops at a
    fresh `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`;
  - *broad* → creates a child work item `<parent-id>-remediation-<n>` and
    hands you its whole own command sequence, starting `/milestone-plan
    <child-id>`; nothing is implemented inline. See [Remediation
    children](#remediation-children-a-child-work-items-own-cycle).
- **Writes**: source/tests, checklist, `WORKFLOW_STATE.json`, commits, bundle.
- **Next**: back to the functional gate, or to a fresh implementation-review
  round if any finding took the bounded branch (the stop is for the whole
  round, not just that finding).
- **Caveat**: enhancements are flagged for you, never silently implemented.

### `/accept-milestone [work-item-id]` — user-only
- **When**: functional review is clean **and** every checkpoint in the item's
  own registry is `COMPLETE`.
- **Expects**: `AWAITING_FUNCTIONAL_REVIEW` (or `AWAITING_USER_ACCEPTANCE`)
  and a terminal registry.
- **Does**: closes out the milestone.
- **Writes**: `WORKFLOW_STATE.json` (`MILESTONE_COMPLETE`,
  `active_work_item_id` cleared), `docs/ROADMAP.md`,
  `docs/ACTIVE_MILESTONE.md`, archives plans to `docs/milestones/completed/`,
  completion commit.
- **Next**: `MILESTONE_COMPLETE`; next action is `/milestone-plan`.
- **Refuses**: no literal confirmation naming the work item and the
  `acceptance` stage; any child work item not itself `MILESTONE_COMPLETE`; any
  of the item's own checkpoints not `COMPLETE` (the refusal names it, and
  points you at `/milestone-implement` or, for a functional-review finding,
  at `/apply-functional-review`'s bounded/broad branches); a registry that
  fails to resolve or does not declare this work item; **any completion
  obligation the item's own registry declares that does not derive
  `PASS`** — `UnsatisfiedCompletionObligationError`, naming each
  outstanding obligation, its classification, and for a `FAIL` the
  specific failing conformance assertions. That last one is independent of
  the other two: marking every checkpoint `COMPLETE` does not satisfy it.
  The live registry today carries `WFO-STATE-SERIALIZATION` and
  `WFO-LEDGER-COVERAGE`; an item whose registry declares none passes it
  vacuously. Also refuses a plan-stage protected file edited since its own
  approval (`StalePlanApprovalRegistryReadError`) — a registry cannot be
  widened after the fact. None of these refusals touch the roadmap or
  commit.
- **Remediation children**: an item with a `parent_work_item_id` is not a
  roadmap milestone, so the roadmap/archive/next-action steps are skipped
  and its completion is recorded in the parent's own functional-review
  checklist instead.

### `/prepare-review <base-sha> <stage> [work-item-id]`
- **When**: a one-off review of work that is not part of a tracked milestone
  checkpoint.
- **Expects**: nothing — no phase requirement. One of the two commands
  unconditionally declaring `state_writer: false`; the other is the
  report-only review command `/review-functional` (writes nothing at all).
  `/review-implementation` (`workflow-2.5.0`) is no longer among them: its
  frontmatter now declares `state_writer: true`, because for a `"2.2"` item
  at `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` it is the authoritative
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage writer. It still writes
  `REVIEW_FEEDBACK.md` and nothing else, exactly as before, for a
  `"1"`/`"2.1"` item or a `"2.2"` item outside that phase -- the declaration
  is membership in `discover_state_writers`' writer census, whose vocabulary
  is the closed set `true`/`false`/`"publisher"` (a missing or unparseable
  value fails closed, so there is no "conditional" to declare), never a claim
  that every invocation writes. `/review-plan` is *not* one of them either:
  it records a ledger stage and a phase, so it declares `state_writer: true`
  on every branch it has.
- **Does**: writes the author-side bundle files and runs
  `scripts/prepare-ai-review.sh <base-sha> <stage> [work-item-id]`.
- **Writes**: bundle files only. No state, no commits.
- **Next**: nothing automatic.
- **Caveat**: for gated reviews use the milestone commands instead — they
  populate the bundle correctly for each state. `work-item-id` is required
  when the stage is `plan`.

### `/bootstrap-workflow-v2`
- **When**: driving the `workflow-v2-1-core` work item only (hardcoded, never
  an argument, and the one command that deliberately takes no
  `[work-item-id]`). It is the **sole** driver for that item, and its own
  file says it is "retired — deleted — only at this work item's own
  `MILESTONE_COMPLETE`". That condition is now met — `workflow-v2-1-core`
  is `MILESTONE_COMPLETE` — but the file has not been deleted, so the
  command is still on disk and still in this reference's roster of 16.
  Retiring it is a scoped act of its own (the way
  `/accept-scoped-remediation`'s retirement was); until then, treat it as
  live-but-unreachable: its target is terminal, so every invocation refuses
  (ledger `O16`).
- **Expects**: no open plan-approval journal; a reachable implementing entry.
- **Does**: implements exactly one checkpoint per invocation, then stops —
  never loops, never hands off to `/milestone-implement`. Never reads
  `docs/ACTIVE_MILESTONE.md` or `docs/ROADMAP.md`. On the invocation where no
  checkpoint remains, it runs that item's own Python test suite and generates
  the implementation bundle.
- **Writes**: source/tests, `WORKFLOW_STATE.json`, checkpoint commits, a
  bundle-generation-record commit, bundle files.
- **Next**: one checkpoint per invocation; requires a **fresh session** to
  continue.
- **Caveat**: this item is permanently `governing_workflow_version: "1"`.

---

## Shared refusal conditions

These apply across most commands and are usually what you are hitting:

- **`REJECTED` bundle marker** — every bundle consumer asserts
  `assert_bundle_not_rejected` twice (once on read, once immediately before
  its first write). An unreadable marker counts as present.
- **Stale bundle** — the recomputed `bundle_id`/`review_content_id` must
  match what `MANIFEST.md`/`REVIEW_REQUEST.md` claim.
- **Wrong worktree or HEAD** — `assert_local_generation_matches` compares
  `MANIFEST.md`'s recorded `worktree_root`/`generation_head` against the
  live ones. This is a different check from the bundle id; both run.
- **Binding fields** — every review round's `REVIEW_FEEDBACK.md` must carry
  `Reviewed bundle ID:`, `Reviewed base commit:`, and `Work item:`, matching
  the current values. `FUNCTIONAL_REVIEW.md` has no binding requirement.
- **Wrong phase** — commands name the actual phase and stop rather than
  guessing or silently re-running.

## Phases

`KNOWN_PHASES` in `scripts/workflow_state.py` is an **allowlist, not a
transition graph** — its own comment says so. Twenty names are declared
(`workflow-2.4.0` added `AMENDING_PLAN`, additive; `workflow-2.5.0` added
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`/
`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, also additive); sixteen
are ever written into `work_items[<id>].phase`. Confusing the two
sets is the single most common way to misread this workflow, so the census
below is derived mechanically and kept honest by
`workflow_state_test.TestPersistedPhaseWriterCensus` (an AST walk of every
phase assignment) and independently by
`workflow_integration_test.TestWorkItemTargetingContract`.

**Persisted phases** — a work item really sits here, and
`WORKFLOW_STATE.json` really says so:

| Phase | Written by |
|---|---|
| `PLANNING` | `default_work_item` (a freshly created work item, including a remediation child) |
| `AWAITING_EXTERNAL_PLAN_REVIEW` | `publish_plan_revision`, `"1"` branch |
| `AWAITING_LOCAL_PLAN_REVIEW` (2.1) | `publish_plan_revision` `"2.1"` branch; `transition_to_awaiting_local_plan_review` |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` (2.1) | `record_local_plan_review` on `APPROVE` |
| `REVISING_PLAN` (2.1) | `record_local_plan_review` / `record_manual_plan_review`, both on `REVISE` — both refuse a `"1"` item, so that version never occupies it either |
| `AWAITING_PLAN_APPROVAL` (2.1) | `record_manual_plan_review` on `APPROVE` — a `"1"` item never reaches it; for that version it is a gate name only |
| `IMPLEMENTING` | `apply_plan_approval` |
| `SELF_REVIEWING_IMPLEMENTATION` | `complete_checkpoint`, when that completion makes every checkpoint `COMPLETE`; `enter_self_reviewing_implementation`, the wrap-up writer for the case where they already all are (e.g. after a plan re-approval) |
| `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` (2.2, `workflow-2.5.0`) | `record_bundle_generation`'s version-dependent `bundle_generation_target_phase` resolver — first-round and post-fix alike; no separate transition writer |
| `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` (2.2, `workflow-2.5.0`) | `record_local_implementation_review` on `APPROVE` |
| `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `record_bundle_generation` — every `"1"`/`"2.1"` stage/outcome; for `"2.2"`, `record_manual_implementation_review` on `APPROVE` (the terminal "ready for approval" phase, reused rather than a new name) |
| `APPLYING_REVIEW_FEEDBACK` | `enter_applying_review_feedback`, for every version -- but for `"2.2"` (`workflow-2.5.0`) the ordinary two-stage `REVISE` loop never reaches this writer: `record_local_implementation_review`/`record_manual_implementation_review`'s own `REVISE` branch already writes this phase directly, so `/apply-implementation-review`'s step 0 finds `phase` already `APPLYING_REVIEW_FEEDBACK` and skips its call under that command's version-independent phase-conditional guard. The `"2.2"` terminal-phase escape -- a late fix committed after both implementation-review stages already `APPROVE`d and the item reached `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` -- does call `enter_applying_review_feedback` |
| `AWAITING_FUNCTIONAL_REVIEW` | `apply_technical_approval`; `promote_legacy_work_item` |
| `MILESTONE_COMPLETE` | `complete_work_item` — the only terminal phase |
| `LEGACY_READY` | `import_legacy_work_item` — dormant, **not** terminal |
| `AMENDING_PLAN` (`workflow-2.4.0`) | `request_plan_amendment` — a re-entry phase, left by the next `/milestone-plan` invocation, not a fresh-item phase |

**Declared but never written** — vocabulary only. Nothing assigns these,
so no work item is ever found at one, and no command can be "run from"
one:

| Phase | What it actually is |
|---|---|
| `SELF_REVIEWING_PLAN` | `/milestone-plan` step 4's in-session self-review. Real work, no persisted stop |
| `AWAITING_TECHNICAL_APPROVAL` | The name of the implementation-stage approval **gate**, whose entry condition is computed by `technical_approval_gate_reachable` while the item sits at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`. `apply_technical_approval` writes `AWAITING_FUNCTIONAL_REVIEW` directly |
| `FIXING_FUNCTIONAL_FINDINGS` | `/apply-functional-review`'s opening line. The item stays at `AWAITING_FUNCTIONAL_REVIEW` unless a bounded fix's `record_bundle_generation` moves it to `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` |
| `AWAITING_USER_ACCEPTANCE` | Accepted for forward compatibility by `milestone_complete_gate_reachable`; in practice acceptance happens from `AWAITING_FUNCTIONAL_REVIEW` |

Note the asymmetry, because it is not obvious: `AWAITING_PLAN_APPROVAL`
has *both* a computed reachability predicate
(`plan_approval_gate_reachable`) **and** a real writer, so it is a
persisted phase; `AWAITING_TECHNICAL_APPROVAL` has only the predicate.

And note the version scoping. Both `REVISING_PLAN` and
`AWAITING_PLAN_APPROVAL` are written only by the two-stage verdict
commands, which refuse a `"1"` item outright. So a `"1"`-governed item's
whole plan lane persists exactly three phases — `PLANNING` →
`AWAITING_EXTERNAL_PLAN_REVIEW` → (`/approve-review plan`) →
`IMPLEMENTING`. For that version, `REVISING_PLAN` and
`AWAITING_PLAN_APPROVAL` are names, like the four above.

**Scope of that claim (`workflow-2.4.0`, `I-R19-1`)**: it describes the
one-shot, pre-approval `PLANNING` → `IMPLEMENTING` sequence only. It says
nothing about `AMENDING_PLAN` (`workflow-2.4.0`): that phase is reached
only *after* a first `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` entry,
by `/request-plan-amendment`, and re-enters this same lane via
`/milestone-plan`'s existing dual-mode branch — a `"1"`-governed item can
be amended too, and re-runs the identical three-phase lane above on its way
back to `IMPLEMENTING`, one or more times.

### "Enter the `X` state" in a command file

Twelve of the seventeen command files open with an `Enter ...` line naming a
phase. It is inherited v1 wording and does **not** mean the command writes
that phase. Three things it can mean, and which command means which —
derived from whether the file actually calls a writer of it:

| Meaning | Commands |
|---|---|
| It really writes that phase | `/accept-milestone` (`complete_work_item`), `/apply-implementation-review` (`enter_applying_review_feedback`, for every version — but for `"2.2"` the ordinary two-stage `REVISE` loop never reaches this writer, since `record_local_implementation_review`/`record_manual_implementation_review`'s own `REVISE` branch already writes `APPLYING_REVIEW_FEEDBACK` directly; the `"2.2"` terminal-phase escape does call it, exactly as the `APPLYING_REVIEW_FEEDBACK` row above states), `/prepare-functional-review` — but only on the `LEGACY_READY` adoption branch (`promote_legacy_work_item`), `/request-plan-amendment` (`request_plan_amendment`, `workflow-2.4.0`), `/review-implementation` (`workflow-2.5.0`, `"2.2"` authoritative branch only — `record_local_implementation_review`), `/record-manual-implementation-review` (`workflow-2.5.0`, `record_manual_implementation_review`) |
| It names the phase the command runs **in**, and writes a different one | `/milestone-plan` (writes `PLANNING` only when creating a fresh item, then moves it on), `/review-plan`, `/milestone-implement`, `/apply-plan-review`, and `/record-manual-plan-review`, whose "Enter the **exit of** `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`" is the one phrasing that says so precisely |
| It names a **gate**, not a phase | `/approve-review` ("Enter `AWAITING_PLAN_APPROVAL` or `AWAITING_TECHNICAL_APPROVAL`" — the first is a real phase for a `"2.1"` item only, the second never; what it writes is `IMPLEMENTING` or `AWAITING_FUNCTIONAL_REVIEW`) and `/apply-functional-review` (`FIXING_FUNCTIONAL_FINDINGS`, which nothing writes at all) |

If you are trying to work out where a work item *is*, read
`work_items[<id>].phase`, never a command's opening line.

**Hard gates** — places you actually stop and wait — are the six
`MILESTONE_WORKFLOW.md` names: `AWAITING_EXTERNAL_PLAN_REVIEW`,
`AWAITING_PLAN_APPROVAL`, `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`,
`AWAITING_TECHNICAL_APPROVAL`, `AWAITING_FUNCTIONAL_REVIEW`,
`AWAITING_USER_ACCEPTANCE`. Two of those six are gate *names* rather than
phases (the last three rows of the table above cover them): at the
technical-approval gate the item's phase reads
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, and at the user-acceptance gate
it reads `AWAITING_FUNCTIONAL_REVIEW`. A `"2.1"` item also stops at the two
plan-review states, so it stops in seven places in practice. A `"2.2"` item
(`workflow-2.5.0`) stops at those same two plan-review states **plus** the
two new implementation-review states
(`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`/
`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`), so it stops in nine
places in practice — do not use the number six to predict stops for any
version.

## Remediation children: a child work item's own cycle

`/apply-functional-review`'s broad branch creates
`<parent-id>-remediation-<n>` — a real, ordinary work item with the
parent's `work_item_type`/`work_item_kind`, its own
`plan_path`/`registry_path`/`base_commit`, `parent_work_item_id` set, and
`governing_workflow_version` fixed from the config default at creation.

Two consequences drive everything else:

1. **The child is never `active_work_item_id`.** Creation deliberately
   does not touch the pointer, and neither does planning the child
   (`route_work_item` claims the pointer only when it is free). The parent
   keeps focus. **So every command you run for the child must name its
   id.** Omit it and you will drive the parent.
2. **The parent cannot complete until the child does.**
   `complete_work_item` refuses with `IncompleteChildWorkItemError` while
   any item names it as `parent_work_item_id` and is not
   `MILESTONE_COMPLETE`. `/accept-milestone <child-id>` is the only thing
   that clears it.

The sequence, in full:

```
/milestone-plan <child-id>              → AWAITING_LOCAL_PLAN_REVIEW   ("2.1")
                                        → AWAITING_EXTERNAL_PLAN_REVIEW ("1")
/review-plan <child-id>                 → AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW
/record-manual-plan-review <child-id>   → AWAITING_PLAN_APPROVAL
/approve-review plan <child-id>         → IMPLEMENTING
/milestone-implement <child-id>  (× N)  → SELF_REVIEWING_IMPLEMENTATION
                                        → AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW ("1"/"2.1")
                                        → AWAITING_LOCAL_IMPLEMENTATION_REVIEW ("2.2")
/review-implementation <child-id>       (optional for "1"/"2.1"; authoritative for "2.2")
                                        → AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW ("2.2" only)
/record-manual-implementation-review <child-id>  ("2.2" only) → AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
/approve-review implementation <child-id> → AWAITING_FUNCTIONAL_REVIEW
/prepare-functional-review <child-id>
/review-functional <child-id>           (optional)
/accept-milestone <child-id>            → MILESTONE_COMPLETE
                                          ...which unblocks the parent
```

A `"1"`-governed child (only possible if `default_workflow_version` was
`"1"` when the broad branch ran) replaces the two plan-review commands
with a single `/apply-plan-review <child-id>` against pasted external
feedback, exactly as any other `"1"` item does; everything below the plan
lane is identical except that it also skips both implementation-review
commands the diagram marks `"2.2" only` (a `"1"` item's terminal phase is
reached directly by `/milestone-implement`'s own bundle-generation step, as
always).

A `"2.2"`-governed child (`workflow-2.5.0`) runs the two implementation-
review commands the diagram marks `"2.2" only`, mirroring the plan lane's
own local/manual-external split exactly, substituted for the
implementation stage; a `"1"`/`"2.1"` child skips both and reaches
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` directly from
`/milestone-implement`'s own bundle-generation step, running
`/review-implementation <child-id>` only optionally and advisorily.

`REVISE` diverts through `/apply-plan-review <child-id>` or
`/apply-implementation-review <child-id>`; the child's own functional
findings go through `/apply-functional-review <child-id>` (its three
branches apply recursively, so a child can have a child —
`<parent>-remediation-1-remediation-1`, and the grandparent stays blocked
transitively); a stale generation head is repaired by
`/recover-implementation-provenance <child-id>`.

The child starts at `PLANNING` with a `null` `mapping_path` and no
artifacts declaration — `/milestone-plan <child-id>` fills both in through
`route_work_item`'s resume branch. Its `base_commit` was fixed at
creation: do not pass a different `<base-sha>`.

**The functional-review checklist is one shared file.**
`FUNCTIONAL_CHECKLIST_PATH` is `docs/ACTIVE_MILESTONE.md` for every work
item, so `/prepare-functional-review <child-id>` overwrites whatever the
parent's own round put there. That is harmless — checklist *evidence* is a
commit (blob plus `Workflow-Functional-Checklist:` trailer), discovered
from history, not from the working tree — but it has two operator
consequences while a child is in flight: `/review-functional <parent-id>`
will report the parent's checklist as drifted from its own evidence blob,
and the parent needs a fresh `/prepare-functional-review <parent-id>`
round after the child completes. That last step is the supported one, and
is what `/accept-milestone <child-id>` reports as the parent's next
action.

Proven end to end by acceptance-matrix rows `C6`/`C7`
(`workflow_acceptance_matrix_test.py`), including a plan-review `REVISE`
round driven while the parent still holds the pointer.

**`workflow-2.4.0`: the child can amend its own plan too.** Once it reaches
`IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION`, running
`/request-plan-amendment <child-id>` followed by `/milestone-plan
<child-id>` re-enters the sequence above at its own `/milestone-plan
<child-id>` step, on exactly the terms `D-Plan-Amendment-1`/`-2` grant any
work item — never `active_work_item_id`, same as every other command on
this child's own lifecycle.

## Known discrepancies

Found while checking this document against the live command files and
`scripts/workflow_state.py`, and re-verified against the current tree each
time this section is touched. Recorded, not fixed — this document changes
no behavior. Items resolved by a later repair are removed, not left
standing.

1. **`/milestone-plan` step 6 names the wrong state for a `"2.1"` item.**
   The step is headed "Enter `AWAITING_EXTERNAL_PLAN_REVIEW`" and step 7 says
   to wait for `REVIEW_FEEDBACK.md`. But for a `"2.1"` item, step 3 has
   already called `publish_plan_revision`, which sets the phase to
   `AWAITING_LOCAL_PLAN_REVIEW` (`workflow_state.py`: `"2.1"` →
   `AWAITING_LOCAL_PLAN_REVIEW`, `"1"` → `AWAITING_EXTERNAL_PLAN_REVIEW`).
   The real next action is `/review-plan`, not an external upload. **The
   code is authoritative**; treat step 6's heading as `"1"`-only wording.
   This is the most likely source of the plan-review confusion.

2. **`MILESTONE_WORKFLOW.md`'s `AWAITING_LOCAL_PLAN_REVIEW` entry condition
   is incomplete.** It lists only "`REVISING_PLAN`'s exit condition is met,
   or `/apply-plan-review` has just applied an accepted plan edit". It omits
   the first entry, straight from `/milestone-plan`'s own
   `publish_plan_revision` call — which is how a fresh `"2.1"` item actually
   arrives there.

3. **`REVIEW_PROTOCOL.md`'s feedback template omits fields the `"2.1"` plan
   commands require, and only one other document fills the gap.** Its
   "Required structure" lists `Status:` and the three binding fields
   (`Reviewed bundle ID:`, `Reviewed base commit:`, `Work item:`) and
   never mentions `Reviewer role:` or the `review_content_id` line — yet
   `/record-manual-plan-review` hard-refuses feedback whose `Reviewer
   role:` does not normalize to `MANUAL_EXTERNAL_PLAN_REVIEW`, and
   `/review-plan` always writes both. `MILESTONE_WORKFLOW.md` documents
   `Reviewer role:` explicitly, with both the canonical and the legacy
   spelling. `PLAN_REVIEW_WORKFLOW.md` does **not**: it discusses
   `review_content_id` (the bundle facts a reviewer is given, and the
   staleness rule) but contains no `Reviewer role:` text at all. An earlier
   revision of this section claimed both documents covered both fields;
   that was wrong about `PLAN_REVIEW_WORKFLOW.md` and is corrected here.

4. **`MILESTONE_WORKFLOW.md`'s "broad remediation" paragraph names the
   `"1"` entry phase for what is normally a `"2.1"` child.** It says the
   child "routes through the full normal cycle (`AWAITING_EXTERNAL_PLAN_REVIEW`
   → ... → `MILESTONE_COMPLETE`)". There is no remediation-specific entry
   rule: the child is an ordinary work item, so `publish_plan_revision`'s
   version branch decides, and a child created under this repository's own
   `default_workflow_version: "2.1"` enters at
   `AWAITING_LOCAL_PLAN_REVIEW`. The named phase is correct only for a
   `"1"`-governed child. See [Remediation
   children](#remediation-children-a-child-work-items-own-cycle).

5. **`/apply-plan-review` step 6 is `"1"`-only wording with no `"2.1"`
   meaning.** It says a `BLOCK` status should "stay in
   `AWAITING_EXTERNAL_PLAN_REVIEW`", a state a `"2.1"` item never occupies.
   On a `"2.1"` item a `BLOCK` never routes into this command at all (neither
   verdict command transitions on `BLOCK`), and step 7' overrides the exit to
   `AWAITING_LOCAL_PLAN_REVIEW` regardless.

6. **`MILESTONE_WORKFLOW.md` gives each of the four unwritten phases its
   own `### <PHASE>` section, with entry/exit conditions.** That is
   consistent with what that document is — a state machine written as
   narrative, predating the persisted-phase vocabulary — but read
   alongside `WORKFLOW_STATE.json` it looks like four states a work item
   can be found at. It cannot: see [Phases](#phases) for which 13 are
   real. The same applies one version narrower to `REVISING_PLAN` and
   `AWAITING_PLAN_APPROVAL`, which that document describes without version
   scoping.

7. **`/approve-review plan` still refuses `workflow-v2-1-core`.** Its step 4a
   interim scope guard refuses while
   `docs/ai-workflow/WORKFLOW_V2_PLAN.md` still contains the heading
   "Bootstrap plan-approval procedure" — which it currently does. That item
   is already `MILESTONE_COMPLETE`, so the guard is moot in practice, but it
   is live code for any future use of that id.
