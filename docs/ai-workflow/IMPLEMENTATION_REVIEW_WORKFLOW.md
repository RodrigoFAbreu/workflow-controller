# Two-Stage Implementation-Review Protocol (`governing_workflow_version: "2.2"` only)

<!-- review-material-lifecycle: CURRENT -->

Concise operator guide for the local-then-manual-external
implementation-review protocol (`workflow-2.5.0`). Full design and
rationale live in `docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s
`D-Implementation-Review-Stages`/`D-Implementation-Review-Version-Activation`;
full state definitions and the verdict/state transition table live in
`docs/ai-workflow/MILESTONE_WORKFLOW.md`. This document only describes the
normal flow and the `"2.2"` activation procedure, operationally — it does
not repeat either design section.

Documents the already-reviewed design for operators; it is not itself the
design (same treatment as `MILESTONE_WORKFLOW.md`/`REVIEW_PROTOCOL.md`) —
writing or editing this file never changes implementation-stage
`review_content_id`.

Scoped entirely to `governing_workflow_version: "2.2"` work items. A
`"1"`/`"2.1"` item (including this repository's own current work items,
and `~/Workspace/workflow-controller`'s `workflow-controller-generation-1`
— see "Activating `\"2.2\"`" below) uses the existing single-stage
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` gate unchanged;
`/review-implementation` stays purely advisory for such an item, and
`/record-manual-implementation-review` refuses cleanly if invoked against
one. Plan review for a `"2.2"` item is unaffected by anything in this
document — it runs the identical two-stage mechanism a `"2.1"` item
already gets; see `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`.

## Normal flow

```text
Sonnet: /milestone-implement  (checkpoint-by-checkpoint, as today)
  ... all checkpoints complete, self-review/verification done ...
  → bundle generation lands the item at AWAITING_LOCAL_IMPLEMENTATION_REVIEW
Opus (fresh session): /review-implementation
  REVISE → Sonnet: /apply-implementation-review → back to AWAITING_LOCAL_IMPLEMENTATION_REVIEW
  APPROVE → continue below
User: upload the exact approved current bundle to a manual external reviewer
User: paste the reviewer's feedback into REVIEW_FEEDBACK.md
Sonnet: /record-manual-implementation-review
  REVISE → Sonnet: /apply-implementation-review → back to AWAITING_LOCAL_IMPLEMENTATION_REVIEW
  APPROVE → AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW (existing terminal phase, reused)
User: /approve-review implementation only after the complete sequence approves
```

`REVIEW_FEEDBACK.md` here always means `<feedback_dir>/REVIEW_FEEDBACK.md`
(`D-Feedback-Layout`, workflow-2.6.0): `.ai-review/<work_item_id>/feedback/`
for every work item created under `2.6.0` or later, by construction; a
legacy item may still resolve the flat `.ai-review/feedback/`
(`REVIEW_PROTOCOL.md` "Feedback directory"). `/record-manual-implementation-review` prints the exact
path to paste into; `python3 scripts/workflow_fingerprint.py
--resolve-feedback-path <work-item-id>` prints it at any time.

A fresh session is strongly recommended for `/review-implementation`, for
the same genuine-independence reason the plan-side local stage recommends
one. This is operational guidance only: no command contract, state
transition, or validator rule depends on detecting session freshness.

## At every stop

Whichever command stops, it states, in its own report:

- the current work item (`work_item_id`);
- the review stage just completed or awaited;
- the bundle path, `bundle_id`, and `review_content_id` relevant to the
  next action;
- the exact next command to run, or the exact manual action required (e.g.
  "upload this bundle to a manual external reviewer").

## What each command does, in one line

- **`/review-implementation`**: dual-mode. For a `"1"`/`"2.1"` item, its
  existing advisory-only behavior (writes `REVIEW_FEEDBACK.md`, never
  `WORKFLOW_STATE.json`, never advances `phase`) is byte-for-byte
  unchanged. For a `"2.2"` item, it is the authoritative
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage writer: independently
  re-verifies the implementation bundle against the repository, writes
  `REVIEW_FEEDBACK.md`, and — for an `APPROVE` only — records the local
  stage in the `implementation_review_stages` ledger and advances to
  `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`. Model-independent:
  nothing in its contract names a specific model.
- **`/record-manual-implementation-review`** (new command): mechanical
  ingestion of an already-pasted manual verdict. Never evaluates
  implementation content itself (the external reviewer already did) and
  never edits source/plan/registry/mapping/bundle content. Not a
  user-authority gate — `/approve-review implementation` remains the sole
  approval gate, unchanged.
- **`/apply-implementation-review`**: unchanged mechanism for a
  `"1"`/`"2.1"` item. For a `"2.2"` item, revised exit: after an accepted
  fix, never self-declares implementation readiness — the post-fix bundle
  regeneration always returns to `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`,
  requiring a fresh local pass before manual-external review can run
  again. This applies identically whether the post-fix round came from
  the ordinary `REVISE` loop or from a `"2.2"` item's functional-review
  bounded-fix branch (`/apply-functional-review`).

## Committing a review-stage write (`workflow-2.9.1`)

A `REVISE` is committed on its own, before any fix commit, so the post-fix
generation record still transitions `phase`; an `APPROVE` is left uncommitted
for the approval commit to take, because a commit of its own puts `HEAD` past
the bundle's `generation_head`. `/apply-implementation-review` step 1 commits
a still-pending `REVISE` write (or step 0's own entry) as a safety net and
does nothing when it is already committed. The full rule, with the helper
and its refusals, is in `REVIEW_PROTOCOL.md` ("Which review-stage writes are
committed"); it does not depend on the governing version.

## Staleness, in one line

Both ledger stages are valid only while the ledger's stored
`review_content_id` equals the freshly recomputed current implementation-stage
one. Any protected implementation-stage content change changes that id, so
both stages read as absent immediately, with no explicit clear step — this
is what makes "any protected implementation edit clears both stages" true
by construction. The manual stage's `bundle_id` check is the one
exception: a mismatch there is advisory only (warns, never blocks), since a
wrapper-only bundle regeneration between upload and paste is expected, not
a staleness signal.

## Activating `"2.2"`

> **Workflow 2.9.0 (2026-10):** a *new* installation now ships
> `WORKFLOW_CONFIG.json` with `default_workflow_version: "2.2"` and
> `supported_versions: ["1", "2.1", "2.2"]`, so the hand-edit below is needed
> only by a repository bootstrapped before 2.9.0 that wants `"2.2"`. An update
> never rewrites an existing `WORKFLOW_CONFIG.json`, and an existing work item
> keeps its governing version. The paragraph that follows describes `2.5.0`'s
> own shipped template and is kept as history.

`"2.2"` is not enabled by a fresh `2.5.0` install: `2.5.0`'s own shipped
`templates/docs/ai-workflow/WORKFLOW_CONFIG.json` stays at `"2.1"`,
unchanged from `2.4.0`'s. A repository must explicitly activate `"2.2"`
before any work item in it can ever be created governed by it.

**The documented, supported procedure is a direct hand edit to
`WORKFLOW_CONFIG.json` — never a call through
`build_activated_config`/`build_rolled_back_config`.** Those helpers are
generalized (`D-Implementation-Review-Version-Activation`) only because
they are the executable specification the unit tests pin the
target-version transform against, and to give a future automated
`workflow_manager` verb a ready-made entry point if one is ever added;
`2.5.0` itself wires no such caller and no `workflow_manager` command
performs this edit for you.

1. Edit `WORKFLOW_CONFIG.json`'s `default_workflow_version` to `"2.2"`
   **and** append `"2.2"` to `supported_versions`, in the same commit —
   `validate_config` refuses a commit that edits only one of the two
   fields.
2. Carry a `Workflow-Activation: 2.2` trailer on that commit (or
   `Workflow-Rollback: 2.2` on a later commit that reverts it — see
   "Rolling back," below).
3. **After this commit, a missing or corrupt `WORKFLOW_CONFIG.json` is a
   hard stop, not a silent fallback** — already true once a repository has
   activated `"2.1"`, and newly true for this repository's own
   configuration the moment it activates `"2.2"`. This is a direct
   consequence of `ConfigMissingAfterActivationError`'s existing guard,
   which this milestone extends to recognize `"2.2"` as activated too; it
   is not a new failure mode this milestone introduces.
4. **The trailer's value is semantically load-bearing.** Write it as
   exactly the activated version string (`2.2`, not a variant). A
   mistyped or blank `Workflow-Activation`/`Workflow-Rollback` trailer
   value is not merely undocumented — it changes which config states the
   tooling treats as activated (see "Rolling back," below).

**`"2.2"` is available only to a work item created after a repository
activates it.** There is no legal path for an already-existing
`"1"`/`"2.1"` work item, in this repository or any other, to become
`"2.2"` retroactively, and `workflow-2.5.0` adds none. In particular:
`~/Workspace/workflow-controller`'s own work item
`workflow-controller-generation-1` (already `"2.1"`-governed) does **not**
become `"2.2"` merely because that repository updates to `2.5.0`, or even
after that repository separately activates `"2.2"` for future work items —
it stays `"2.1"` for the rest of its lifetime, with the single-stage
implementation-review flow unchanged. An operator updating
`workflow-controller` to `2.5.0` must not mistake that item for
automatically upgraded. Only a *new* work item created after activation
gets the two-stage implementation-review flow.

## Rolling back

A `Workflow-Rollback: 2.2` commit reverts `default_workflow_version` to
`"2.1"` (never to `"1"` — the rollback destination is the version
activation superseded, not a fixed literal; a `Workflow-Rollback: 2.1`
commit still reverts to `"1"`, unchanged). `supported_versions` is not
edited by a rollback — a stale member there grants nothing, since version
validation only ever checks the active `default_workflow_version` value
itself, never an independently requested one.

After a `Workflow-Rollback: 2.2` commit, the repository is still
`"2.1"`-configured, and `ConfigMissingAfterActivationError`'s hard stop
correctly stays armed — this is why rollback is version-aware rather than
a binary "activated"/"not activated" flag. An unresolvable
`Workflow-Rollback` trailer value (blank, a typo, or a version this
release does not recognize) resolves as **activated**, fail-closed, never
a silent fall-through to "not activated."

**Downgrade posture applies unchanged.** Once a repository has activated
`"2.2"` and any work item has recorded `implementation_review_stages`
vocabulary an older release's `workflow_state.py` does not have,
downgrading that repository's tooling below the release that introduced
`"2.2"` is unsupported for the same reasons this repository's own
`CLAUDE.md` already states for the `"2.4.0"`→pre-`"2.4.0"` boundary. This
document adds no new downgrade path and repeats no exception to that
existing posture.
