# Workflow v2.3 Follow-ups — Pre-Manager/Bootstrapper Maintenance (Revision 13)

Execution/reference plan for work item `workflow-v2-3-followups`. Governed
by `governing_workflow_version: "2.1"` (the two-stage local-then-manual-
external plan-review protocol applies to this item's own plan-stage
approval — see `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`). Source of
requirements: `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md` (the deferred-
follow-ups log `workflow-v2-3` itself wrote and left untouched, per its own
"Scope decision" sections). `base_commit`:
`fb134ac4f7cdabb7861d170bb61331bb8d9f5a14` — `workflow-v2-3`'s own
`MILESTONE_COMPLETE` acceptance commit, current `HEAD` at the time this
plan was written.

## Scope note

This is the "pre-Manager/Bootstrapper follow-up scope" `WORKFLOW_V2_3_FOLLOWUPS.md`'s
own "Follow-up policy after v2.3" section names as intentionally limited to
three items, plus one explicitly-opportunistic fourth pairing. Nothing in
this plan expands that boundary. In particular, this plan does **not**
introduce `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` or any other new
implementation-review lifecycle state, does not add an implementation-review
ledger/quorum, does not touch `/review-functional`, and does not undertake
any part of the Workflow Manager / Bootstrapper / Controller work those
follow-ups explicitly defer. The hard-gate count (currently 6, per
`docs/ai-workflow/MILESTONE_WORKFLOW.md`'s "Hard gates summary") is
unaffected by every checkpoint below.

## Executing this plan

**This item's own plan-stage approval will trip the exact defect CP1
exists to fix** (`LPR-R1-B01`, round-1 local plan review). Verified live
against `base_commit`: `resolve_plan_stage_approval_commit_paths(repo_root,
"workflow-v2-3-followups", state_path)` resolves a **five-member** set —
the plan doc, the registry JSON, the mapping file, `WORKFLOW_STATE.json`,
and a conditional fifth member,
`docs/ai-workflow/registry/workflow-v2-3-followups-artifacts.json`
(`artifacts_declaration_path`), because that file is both pending
(never yet committed for this item) and fresh (its live bytes match
`artifacts_declaration_sha256`). `/approve-review plan
workflow-v2-3-followups` necessarily runs *before* CP1 can be
implemented — using today's unfixed `approve-review.md`. Its current step
5 will stage the fifth member alone; step 6.1 will then call
`stage_plan_approval_commit_paths` against a non-empty index and raise
`DirtyIndexBeforeStagingError`, deterministically, every retry, inside the
journaled `plan_approval_guarded_mutation` transaction.

There is no way through by retrying step 6.1 as written, and no way
through by fixing `approve-review.md` first — that file is
implementation-stage protected content, and editing it before plan
approval would mean executing CP1 pre-approval. **The operator must
complete this item's own plan-stage approval using the same
single-combined-call workaround `workflow-v2-3`'s own approval used**
(recorded in `WORKFLOW_V2_3_FOLLOWUPS.md`) and CP1 later makes the
command's documented sequence: stage every non-state member — the three
ordinary members plus the fifth member together — in one call to
`workflow_state.stage_plan_approval_commit_paths`, then verify the fifth
member's pinned SHA256 (`verify_staged_blob_sha256`), before proceeding to
step 6.2's state-pin compare-and-swap.

**Rollback/retry path, named explicitly** so a `DirtyIndexBeforeStagingError`
is not diagnosed as a fresh incident: the exception propagates out of
step 6.1's guarded `with` block without advancing progress, straight to
step 6b's rollback (`rollback_plan_approval_transaction`), which resets
the index to `HEAD` and closes the journal — zero `WORKFLOW_STATE.json`
bytes written. Re-running `/approve-review plan` after that reset, using
the single-combined-call sequence above instead of the command's current
two-call text, then succeeds normally.

**A second, independent hazard exists in the same upcoming approval run**
(`LPR-R3-B01`, round-3 local plan review), unrelated to the fifth-member
bug above: `approve-review.md` step 6.4's commit instruction (lines
337–346) never states that the `Workflow-Plan-Approval:`/
`Workflow-Work-Item:` trailer pair must be the commit message's **own
final paragraph**, after any `Co-Authored-By:`/`Claude-Session:` lines —
the requirement `milestone-implement.md:176–181` and
`bootstrap-workflow-v2.md:182–186` both already state verbatim. Without
it, a plausible commit-message shape (trailers before the
`Co-Authored-By:`/`Claude-Session:` footer) makes the approval commit's
own trailers unparseable by `git interpret-trailers --parse`, which is
`discover_plan_approval_commit`'s exact mechanism
(`_discover_trailer_commits` → `_commit_trailers`). The consequence is
worse here than at acceptance: `classify_plan_approval_outcome`
(`scripts/workflow_state.py:2082–2096`) then observes `commit is None`
while live `HEAD != journal['pre_procedure_head']` and returns
`AMBIGUOUS` — step 6a's `AMBIGUOUS` branch is "stop immediately," with no
rollback, journal left open, and the approval commit already on disk but
unrecognized. **Decision: CP1 adds the missing final-paragraph sentence
to `approve-review.md` step 6.4** (one sentence, in the same file CP1
already opens for the fifth-member fix; the golden-hash obligation
already stated for `approve-review.md` below covers the resulting hash
move) — see CP1's design below. `accept-milestone.md`'s identical gap is
**declined for this milestone**, the same disposition as item 2 #8/item 3
#7: it is a different command, outside this milestone's four numbered
scope items, and fixing it is not required to get this item's own next
`/approve-review plan` run through safely. It is recorded here, by name,
so it is not rediscovered as a surprise: `accept-milestone.md`'s own
commit step carries the same missing-final-paragraph gap, and
`workflow-v2-3`'s own `MILESTONE_COMPLETE` commit (`fb134ac`, this item's
own `base_commit`) is a live instance of it (see the baseline record
below).

**Pre-existing red baseline at `base_commit`, recorded explicitly**
(`LPR-R3-B01`/`LPR-R3-I01`, round-3 local plan review): a full run of
`workflow_state_demo_test.py` at `base_commit` reads 45 tests, **FAILED
(failures=1, errors=1)** — before any checkpoint of this plan has landed:

1. `test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set`
   fails because `fb134ac` — `workflow-v2-3`'s own `/accept-milestone`
   commit — writes its `Workflow-Work-Item:` line in its own paragraph
   ahead of `Co-Authored-By:`/`Claude-Session:`, exactly the defect class
   named above, and is not yet in
   `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`. History
   rewriting is not available (the test's own docstring says so, and this
   commit is this item's own `base_commit`). **Decision: CP1 adds
   `fb134ac`'s SHA to `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`**
   (`scripts/workflow_state_demo_test.py`), with the same
   named-not-silent rationale-comment convention the set's existing
   `workflow-v2-1-core` entry already uses ("not this work item's own
   regression") — the test's own equality assertion (not a subset check)
   already guards this constant against silent staleness in both
   directions, so no further test is required.
2. `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`
   errors on `UnclassifiedPathError:
   docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md` — this item's own
   plan document is absent from
   `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`'s
   implementation-stage `excluded_paths`, the file that test classifies
   the live dirty set against (`DEFAULT_ARTIFACTS_PATH`, not this item's
   own declaration). Commit `aaa1247` already set the precedent for
   exactly this gap, adding `WORKFLOW_V2_3_PLAN.md`,
   `WORKFLOW_V2_1_OPERATOR_REFERENCE.md`, and `WORKFLOW_V2_3_FOLLOWUPS.md`
   to the same list for the same reason. **Decision: CP1 adds
   `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md` to that same
   `excluded_paths` list**, matching `aaa1247`'s rationale shape exactly —
   `docs/ai-workflow/registry/` is an implementation-stage excluded prefix
   in this item's own artifacts declaration, so this edit classifies
   cleanly and cannot stale this item's own `technical_approval`. This
   failure is otherwise transient (`any_protected_path_dirty` only sees
   *dirty* paths, so it self-clears the moment `/approve-review plan`
   commits the plan doc, and does not recur during CP1–CP4 even if left
   unfixed) but is red right now, at the moment this bundle is reviewed.

With both dispositions above executed inside CP1, `workflow_state_demo_test.py`
reads fully green by the time CP2's own required run of it (below)
executes — CP2's and CP4's "zero regressions" gates need no
baseline-relative carve-out; a plain full pass is the bar.

**Lease naming, noted for operators** (`LPR-R3-O03`, round-3 local plan
review): every guard this item's own `/approve-review plan` run acquires
publishes a lease at `.ai-review/runtime/PLAN_APPROVAL_MUTATION.lease`
whose body hardcodes `"work_item_id": "workflow-v2-1-core"`
(`scripts/workflow_state.py:2340`, pre-existing, unrelated to this
item's own checkpoints and out of scope to fix here) — nothing reads that
field back, so this is cosmetic, but an operator inspecting the lease
file mid-incident should not be confused by seeing a different work
item's id than their own.

## Requirements (from the user's brief, verbatim scope)

1. Fix `/approve-review plan`'s conditional fifth-member staging bug.
2. Change `/review-implementation` so it writes the authoritative current
   `REVIEW_FEEDBACK.md` directly, following `/review-plan`'s operator
   ergonomics but without adding plan-review ledger/state semantics.
3. Normalize persisted review-role / review-stage identifiers to canonical
   `SCREAMING_SNAKE_CASE` with backward-compatible reading where required.
4. If convenient while touching `/review-implementation`, apply the
   existing deferred plan-conformance-read follow-up recorded in the same
   file, but do not expand into new lifecycle states or controller-oriented
   review orchestration.
5. Explicitly out of scope: changes to `/review-functional`;
   `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`; new implementation-review ledger
   stages; reviewer quorum; controller-specific lifecycle redesign; broader
   workflow v3 work.
6. Keep this a focused maintenance milestone and preserve the proven
   lifecycle.

## Independent inspection — what the current implementation actually does

Grounded by direct reads of the affected files (line numbers as of
`base_commit`); nothing below is inferred from `WORKFLOW_V2_3_FOLLOWUPS.md`'s
prose alone.

### Item 1: the fifth-member staging bug is real and structurally guaranteed to recur

`.claude/commands/approve-review.md`'s plan-stage approval sequence is a
crash-resumable, journaled failure-atomicity transaction (`WF8c`), not a
simple linear script. Each mutating step runs inside its own
`workflow_state.plan_approval_guarded_mutation(repo_root, owner_token=...,
step=<label>, now=...)` context manager (`scripts/workflow_state.py:2512`),
which acquires a lease, asserts journal ownership, yields, then advances a
durable per-step progress record before releasing. **Corrected,
`LPR-R3-B02` (round-3 local plan review):** the `step=` label is *not*
purely diagnostic. `acquire_plan_approval_guard`
(`scripts/workflow_state.py:2337`) opens with `step_class =
plan_approval_step_class(step)`, a fail-closed lookup against two
exhaustive frozensets, `PLAN_APPROVAL_DESTRUCTIVE_STEPS` and
`PLAN_APPROVAL_ORDINARY_STEPS` (`scripts/workflow_state.py:2200–2214`) —
an unrecognized label raises `PlanApprovalGuardUnavailableError` rather
than being guessed. The resolved class is written into the lease body and
consumed by real logic (the takeover-rules decision table is
`acquire_plan_approval_guard`'s own docstring at
`scripts/workflow_state.py:2330–2336`; enforcement — refusing
unconditionally on `"destructive"` and conditionally on `"ordinary"` — is
at 2381 and 2631 (**corrected**, `LPR-R5-O01`, round-5 local plan review:
the prior citation, 2306–2315, named the guard-lock context manager's tail
and the function signature, not the decision table or its enforcement)),
and the label itself is quoted
back to a human inside
`plan_approval_guard_release_authorization_literal` (2579–2581). It is
also, separately, recorded by `_advance_plan_approval_owner_progress` as
a human-readable audit tag surfaced in a stuck-transaction takeover
report — both things are true; only "never dispatched on by name
elsewhere" was wrong. `classify_plan_approval_outcome`
(`scripts/workflow_state.py:2053`) does classify purely from live Git
state (commit trailers, index contents), never from step labels — that
half of the original claim stands. This matters directly for the fix
design below: merging two guarded steps into one is a pure choreography
change with no journal-schema or outcome-classification consequence
**only if the merged window's own step label is itself a classified,
already-`"ordinary"` member of `PLAN_APPROVAL_ORDINARY_STEPS`** — see
CP1's design below for which label it uses.

Today's two relevant steps, `.claude/commands/approve-review.md`:

- **Step 5** (lines 287–301, `step="step-5-declaration-pin"`): *if* step 4a
  resolved a conditional fifth member (`workflow_fingerprint.resolve_plan_stage_approval_commit_paths`,
  `scripts/workflow_fingerprint.py:988–1060`, returns a `PlanApprovalCommitPlan`
  with `artifacts_declaration_path`/`artifacts_declaration_sha256` when the
  item's own `<id>-artifacts.json` is both pending and fresh), stage
  *only* it: `workflow_state.stage_plan_approval_commit_paths(repo_root,
  (fifth_member_path,))`, then `workflow_state.verify_staged_blob_sha256(...)`.
- **Step 6.1** (lines 302–312, `step="step-6.2-stage-ordinary"`): stage
  the plan doc, registry JSON, and mapping file — the resolved set *minus*
  `WORKFLOW_STATE.json` and minus the fifth member already staged by step
  5 — via a **second, independent call** to
  `workflow_state.stage_plan_approval_commit_paths(repo_root, ordinary_paths)`.

`stage_plan_approval_commit_paths` (`scripts/workflow_state.py:1640–1689`)
requires the whole Git index to be empty of any diff against `HEAD` before
it runs (`DirtyIndexBeforeStagingError`, lines 1673–1679) and asserts the
post-staging diff is a subset of the paths it was given
(`UnexpectedStagedPathSetError`, lines 1680–1689). Because step 5 leaves
the fifth member staged, step 6.1's own empty-index precondition is false
by construction whenever a fifth member exists — `DirtyIndexBeforeStagingError`
every time, exactly as `WORKFLOW_V2_3_FOLLOWUPS.md` documents happened for
real during `workflow-v2-3`'s own plan approval. **The function itself
already accepts an arbitrary-length path tuple** — nothing about its
signature limits it to one call per member; the two-call split is a pure
command-choreography choice, not a technical necessity.

No existing test exercises the real command's actual two-call choreography
against a genuine five-member fixture:
`TestPlanStageApprovalCommitMembership.test_declaration_never_committed_resolves_five_member_set`
(`scripts/workflow_integration_test.py:1699`) stages all five members in
one combined call (line 1759) — incompatible with the bug, since it never
reproduces the two-call split. `TestPlanApprovalPermanentSiteEndToEnd`
(line 3241) exercises the real step-by-step choreography end to end but
its fixture explicitly asserts `artifacts_declaration_path is None` (lines
3286–3288, "no fifth member in this fixture") — the real bug's precondition
never arises in that suite either. This is the gap
`WORKFLOW_V2_3_FOLLOWUPS.md` item 1 names and this plan's CP1 closes.

`stage_plan_approval_commit_paths` has exactly one production caller
(`.claude/commands/approve-review.md`, steps 5 and 6.1) — no other command
references it, so narrowing its two call sites to one cannot affect any
other command.

### Items 2 and 4: `/review-implementation` vs. `/review-plan`

`/review-plan` (`.claude/commands/review-plan.md`) already does what item 2
wants, at the plan stage: step 8 ("Write set, exact.", lines 77–95)
re-calls `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
work_item_id)` *immediately before the first write* — "a withdrawal
landing between step 5 and here must still be caught" — then writes
`<feedback_dir>/REVIEW_FEEDBACK.md` directly (a plain file write; **no
Python function composes the markdown itself** — confirmed no
`write_review`/`compose_review`/`render_review_feedback` function exists
anywhere in `scripts/*.py`). Only the plan-specific *ledger* half is a real
function call: `workflow_state.record_local_plan_review(state,
work_item_id, *, verdict, bundle_id, review_content_id, round, now)`
(`scripts/workflow_state.py:10420`), which writes into
`plan_review_stages` and transitions `phase` — this is exactly the part
item 2 says must **not** be imported into `/review-implementation`.

`/review-implementation` (`.claude/commands/review-implementation.md`)
today: reads eight bundle artifacts (**corrected from "nine," `LPR-R7-O03`,
round-7 local plan review** — `REVIEW_REQUEST.md`, `IMPLEMENTATION_SUMMARY.md`,
`TEST_RESULTS.md`, `CHANGED_FILES.txt`, `COMMITS.txt`, `DIFF.patch`,
`files/`, `MANIFEST.md`) plus required-context files and prior
feedback (step 3, lines 47–50) — no `PLAN.md`, no `plan_path`; recomputes
fresh identity and calls `assert_bundle_not_rejected` exactly **once**, at
the end of step 4, immediately before *reporting* (not writing); searches
for correctness/layering/migration/test/usability issues (step 5, lines
151–160) with no plan-conformance arm; composes a `REVIEW_FEEDBACK.md`-
shaped report with the same three binding fields `/review-plan` uses, plus
a non-binding `Reviewed review content ID:` line, but **no** `Reviewer
role:` field (confirmed: `apply-implementation-review.md` never checks
one — that vocabulary is plan-stage-specific, checked only by
`/record-manual-plan-review` and `validate_manual_plan_review_preconditions`,
`scripts/workflow_state.py:10494`); then step 7 (lines 177–188) explicitly
prints it and writes nothing.

The binding fields both commands' output is validated against are parsed
generically by `workflow_fingerprint.parse_review_feedback_binding_fields`
(line 2599) and cross-checked by `assert_feedback_matches_bundle` (line
2617) — both stage-agnostic, so `/review-implementation`'s new output
needs no new parser, only to actually reach disk. `resolve_feedback_dir`
(`scripts/workflow_fingerprint.py`, independent of `resolve_bundle_dir`)
already resolves the correct scoped-else-flat path for any stage,
including the implementation stage.

No "replace stale feedback from an older bundle" helper exists anywhere in
the codebase today (confirmed by exhaustive grep). `/review-plan` doesn't
need one either: its safety comes from the fresh `bundle_id`/
`review_content_id` recompute and `assert_local_generation_matches`, both
run once at step 5, plus the second `assert_bundle_not_rejected` call,
re-called immediately before the unconditional overwrite at step 8 —
**corrected**, `LPR-R5-I01` (round-5 local plan review): only that second
call is genuinely immediate; the recompute and generation check are not
re-run a second time right before the write, and `assert_feedback_matches_bundle`
failing closed downstream against whatever bundle is actually being
approved is what covers that gap — not from inspecting the old file. This
is the design item 2's "safe replacement semantics" requirement should
reuse rather than reinvent (see CP2 design below).

Item 4's target text is confirmed to not exist: `/review-implementation`
step 3's eight-artifact read list has no plan document, and step 5's search
list has no plan-conformance arm — unlike `/review-plan` step 4 ("the
authoritative plan doc (`plan_path`)") and step 6 ("independently verify
every finding the plan document claims as addressed... exactly as
thoroughly as `/apply-plan-review`'s own validation requirement"), which
are the sibling instructions to mirror.

### Item 3: exactly two tokens, no enum, and no live non-terminal record holds them today

Exhaustive grep across `scripts/*.py`, `.claude/commands/*.md`,
`docs/ai-workflow/*.md`, and `docs/ai-workflow/WORKFLOW_STATE.json` found
exactly two persisted lowercase tokens in live use: `local_model_plan_review`
and `manual_external_plan_review`. Both are scattered string literals — dict
keys and direct `==`/`!=` comparisons in `scripts/workflow_state.py`
(write sites: `record_local_plan_review` lines 10427–10451,
`record_manual_plan_review` lines 10547–10566; read/comparison sites:
`plan_approval_gate_reachable` lines 8739–8752,
`validate_manual_plan_review_preconditions` lines 10465–10517 including the
hard `feedback_role != "manual_external_plan_review"` comparison at line
10492, `_validate_plan_review_stages` lines 10787–10809) — **no enum or
constant class defines them anywhere**. Command-level mentions:
`.claude/commands/review-plan.md` (the literal template line `Reviewer
role: local_model_plan_review`, line 67) and
`.claude/commands/record-manual-plan-review.md` (its own exact-match
expectation). Canonical live docs mentioning them:
`docs/ai-workflow/MILESTONE_WORKFLOW.md`, `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`.

Critically: `docs/ai-workflow/WORKFLOW_STATE.json`'s **only** live
occurrence of either token as actual JSON data, *as of `base_commit`*, is
inside `work_items["workflow-v2-3"].plan_review_stages` — and that work
item's `phase` is `MILESTONE_COMPLETE`, a terminal phase
(`TERMINAL_PHASES = frozenset({"MILESTONE_COMPLETE"})`,
`scripts/workflow_state.py:296`). At `base_commit`, this item's own
`plan_review_stages` is still `null` (`REVISING_PLAN` has no ledger yet).

**That stops being true before CP3 ever runs** (`LPR-R1-I03`, round-1
local plan review). `record_local_plan_review`'s `APPROVE` branch
(`scripts/workflow_state.py:10441–10451`) writes the literal keys
`"local_model_plan_review"` and `"manual_external_plan_review"` into
`plan_review_stages`, and this item is `"2.1"`-governed: its own
two-stage plan-review protocol (`/review-plan`, then
`/record-manual-plan-review`) writes that ledger during *this item's own*
plan approval — necessarily before CP1, let alone CP3. By the time CP3
lands, `workflow-v2-3-followups` itself will hold both legacy lowercase
keys while genuinely non-terminal (`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`
or later) — **and CP3 migrates this item's own already-written ledger in
place**, since its `phase` is not in `TERMINAL_PHASES` (`LPR-R2-I02`,
round-2 local plan review, superseding this paragraph's own earlier "does
not migrate" disposition — see CP3's design below for the migration
mechanism). `workflow-v2-3`'s own terminal `plan_review_stages` record is
the one CP3 leaves unmigrated, relying instead on the same
compatibility-read helper. So legacy-key reads are on the live path only
for the window between this item's own plan approval and CP3's migration
landing — not indefinitely, and not only a courtesy to closed history. The
work is: (a) write only the canonical casing from now on, (b) make every
reader tolerant of the legacy casing so `workflow-v2-3`'s own
permanently-unmigrated terminal record, any hand-authored fixture, any
legacy-cased state observed in the window before this item's own ledger is
migrated, and any hand-typed legacy `Reviewer role:` paste all remain
understandable without being rewritten, and (c) update the small set of
live command/doc templates that currently emit the lowercase form.

Immutable and explicitly **not** to be touched by this item (historical
review evidence tied to now-terminal work items, or untracked unrelated
working-tree content):
`docs/ai-workflow/WORKFLOW_V2_PLAN.md`, `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`,
`docs/ai-workflow/dry-run/*.md`, every `docs/ai-workflow/registry/*.json` /
`docs/ai-workflow/requirements/*.json` prose mention, `workflow-v2-3`'s own
`plan_review_stages` block inside `WORKFLOW_STATE.json`, and the untracked
`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` /
`docs/ai-workflow/diagrams/` (per CLAUDE.md's "don't touch unrelated
working-tree changes" — these are concurrent scratch content left over
from a prior session, not this item's own).

## Design

### CP1 — `/approve-review plan` fifth-member staging fix

Replace steps 5 and 6.1 in `.claude/commands/approve-review.md` with a
**single** guarded window that stages every non-state approval member —
ordinary members and the conditional fifth member alike — in one call to
`workflow_state.stage_plan_approval_commit_paths(repo_root, ordinary_paths
+ ((fifth_member_path,) if fifth_member_path else ()))`, then, if a fifth
member was resolved, immediately calls
`workflow_state.verify_staged_blob_sha256(repo_root, fifth_member_path,
pinned_sha256)` inside that same window before it advances progress. This
is exactly the safe workaround `WORKFLOW_V2_3_FOLLOWUPS.md` documents
using live, reworded as the command's own new documented sequence rather
than an undocumented recovery procedure. No change to
`stage_plan_approval_commit_paths` or `verify_staged_blob_sha256` is
required — they already support this call shape.

**Merged window's step label, decided explicitly** (`LPR-R3-B02`,
round-3 local plan review): the merged window acquires the guard under a
**new** label, `"step-5-stage-and-pin"`, added to
`PLAN_APPROVAL_ORDINARY_STEPS` in `scripts/workflow_state.py` in the same
commit as the command-choreography change — correcting revision 3's "no
other primitive" claim above. Neither existing label fits: `"step-5-declaration-pin"`
names only the fifth-member pin, `"step-6.2-stage-ordinary"` names only
the three ordinary members, and the merged window does both. This is a
one-line addition to an already-exhaustive, fail-closed frozenset, not a
schema change — `acquire_plan_approval_guard`'s dispatch
(`plan_approval_step_class`), the takeover rules, and
`plan_approval_guard_release_authorization_literal` all continue to work
unchanged once the new label is classified `"ordinary"`, the same class
`"step-5-declaration-pin"` and `"step-6.2-stage-ordinary"` already carry.
**`"step-6.2-stage-ordinary"` is left in `PLAN_APPROVAL_ORDINARY_STEPS`,
unacquired by any real command step, rather than removed**: seven
`workflow_integration_test.py` call sites
(2524/2543/2612/2759/3293/3413/3457) use that literal directly to
exercise the guard/journal mechanics generically, and removing the entry
would require touching all seven for a purely cosmetic gain outside this
checkpoint's own minimal-change scope — the same class of hand-maintained-
surface drift `GPT-IR4-O01` names and this plan's Self-review already
declines to remediate broadly for this milestone.
**`"step-5-declaration-pin"` gets the same disposition, for the same
reason** (`LPR-R4-I03`, round-4 local plan review): the merge replaces
step 5 as well as step 6.1, so this label is retired from any real command
step by exactly the same edit, and CP1's minimal-change scope leaves it in
`PLAN_APPROVAL_ORDINARY_STEPS`, unacquired, rather than removing it. Its
own call-site count is larger, not smaller: `workflow_integration_test.py`
uses the literal directly at 17 sites (2416, 2486, 2490, 2508, 2518, 2538,
2554, 2559, 2562, 2575, 2600, 2612, 2689, 2729, 2751, 2861, 2875),
including two that assert it as an exact-literal return value
(`self.assertEqual(lease["step"], "step-5-declaration-pin")` at 2490,
`self.assertEqual(progress["step"], "step-5-declaration-pin")` at 2562).
**Corrected, `LPR-R5-I02` (round-5 local plan review):** the
`PLAN_APPROVAL_DESTRUCTIVE_STEPS`/`PLAN_APPROVAL_ORDINARY_STEPS` comment
(`scripts/workflow_state.py:2190–2199`) makes a one-way exhaustiveness
claim, not a correspondence one — every step any part of the transaction
acquires the guard for must be named in one of the two frozensets, on pain
of `plan_approval_step_class` refusing rather than guess; it never claims
the converse, and no "one-to-one correspondence" phrase appears anywhere
in the comment or elsewhere in the repository. CP1 preserves that
exhaustiveness guarantee by adding `"step-5-stage-and-pin"` as a new
entry rather than repurposing an existing one. The same comment separately
enumerates which declared entries are not yet acquired by any real command
step; CP1 does not update that enumeration to add `"step-5-declaration-pin"`
and `"step-6.2-stage-ordinary"` to it — the same minimal-change choice
already made for the two frozensets themselves, knowingly left stale
rather than edited for a purely cosmetic gain outside this checkpoint's
scope, exactly as closing the comment's own drift belongs to
`GPT-IR4-O01`'s deferred item.

Explicitly preserved, unchanged:

- the empty-index / dirty-index precondition (`DirtyIndexBeforeStagingError`);
- the post-staging subset assertion (`UnexpectedStagedPathSetError`);
- the conditional fifth-member pinned-SHA verification, now inside the
  merged window instead of its own;
- the durable approval journal and its owner/progress mechanics, apart
  from the one new classified label above — `classify_plan_approval_outcome`
  remains independent of step labels, as corrected in "Independent
  inspection" above;
- step 6.2's state-pin compare-and-swap, step 6.3's exact-staged-set
  assertion, and step 6.4's commit — unchanged in shape, still running
  strictly after the merged staging step (step 6.4 gains one sentence,
  see below);
- step 6b's rollback (`rollback_plan_approval_transaction`) — unaffected,
  since it resets from live Git/journal state, not from step labels.

One further, necessary text correction: step 6a's amend-recovery
paragraph currently says it "unconditionally re-run[s] 6.1's staging and
6.2's compare-and-swap-and-pin" — since step 6.1 no longer exists as a
separate step, this must be reworded to re-run the merged staging step in
full (re-staging already-correct bytes for any untouched member remains a
harmless no-op, exactly as today).

**Trailer final-paragraph sentence, added to step 6.4** (`LPR-R3-B01`,
round-3 local plan review; see "Executing this plan" above for the full
hazard and the decision not to extend this to `accept-milestone.md`):
step 6.4's commit instruction gains the same sentence
`milestone-implement.md:176–181`/`bootstrap-workflow-v2.md:182–186`
already state verbatim — the `Workflow-Plan-Approval:`/
`Workflow-Work-Item:` trailer pair must be the commit message's own final
paragraph, after any `Co-Authored-By:`/`Claude-Session:` lines, never
before them. Add a doc-assertion (new method on
`TestGoldenCommandFileHashes` or a sibling static-conformance class,
matching existing class conventions) confirming `approve-review.md`
states this requirement — genuinely new coverage, since no existing test
asserts it for `milestone-implement.md`/`bootstrap-workflow-v2.md`
either.

**Baseline housekeeping, folded in** (`LPR-R3-B01`/`LPR-R3-I01`, round-3
local plan review; full detail in "Executing this plan" above): in the
same commit as the above,
add `fb134ac4f7cdabb7861d170bb61331bb8d9f5a14` to
`_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`
(`scripts/workflow_state_demo_test.py`) with a rationale comment matching
the set's existing entry's convention, and add
`docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md` to
`docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`'s
implementation-stage `excluded_paths`, matching commit `aaa1247`'s
precedent for the three sibling paths already there. Both are outside
`approve-review.md` itself but land in this checkpoint because CP1 is the
earliest point at which fixing them removes the pre-existing red baseline
before CP2's required `workflow_state_demo_test.py` run (below).

**Conformance-surface obligation** (`LPR-R1-B02`): this checkpoint changes
`approve-review.md`'s real bytes. That file is one of the twelve entries
`workflow_integration_test.py`'s `_GOLDEN_COMMAND_FILE_SHA256` /
`TestGoldenCommandFileHashes` pins by sha256 of the file's literal
content — **verified directly against the dictionary's own AST this
round, twelve is correct** (`LPR-R3-O01`, round-3 local plan review: the
reviewer's own recount of eleven omitted `bootstrap-workflow-v2.md` from
its list; the dictionary's own comment — "the **ten** entries below that
predate WF8c item (h)" plus "the **two** entries below that were created
after" — independently sums to twelve, matching this plan's original
count; no change made). CP1 must update
`_GOLDEN_COMMAND_FILE_SHA256["approve-review.md"]` to the new post-edit
hash, in the same commit as the command-file edit, with the dictionary's
own rationale-comment convention (a short comment explaining why the hash
moved) — never left as an unrelated red test for a later checkpoint to
discover.

Regression coverage (`scripts/workflow_integration_test.py`, in
`TestPlanStageApprovalCommitMembership` and/or
`TestPlanApprovalPermanentSiteEndToEnd`, matching existing class
conventions):

- **Positive, real choreography**: a genuine five-member fixture (fifth
  member present and both pending+fresh) driven through the *actual*
  merged command sequence (not the already-existing single-call low-level
  test) with real Git staging, proving `approve-review.md`'s documented
  sequence now succeeds end to end — the exact gap
  `WORKFLOW_V2_3_FOLLOWUPS.md` confirmed no test covers today.
- **Negative**: unrelated pre-staged content still triggers
  `DirtyIndexBeforeStagingError` before the merged call runs — the merge
  does not weaken the precondition.
- **No-fifth-member path unaffected**: extend or re-run
  `test_end_to_end_happy_path_matches_the_new_permanent_site_procedure`
  and confirm it still passes unmodified in behavior (single call over
  four members, `fifth_member_path is None`).
- **Rollback/retry**: a staging or blob-verification failure inside the
  merged window still leaves the journal in a state step 6b's rollback
  resets correctly (index back to `HEAD`, journal closed, zero
  `WORKFLOW_STATE.json` bytes written).
- **New step label classifies and acquires** (`LPR-R3-B02`): a case
  proving `plan_approval_step_class("step-5-stage-and-pin")` returns
  `"ordinary"` and that `plan_approval_guarded_mutation` successfully
  acquires the guard under it — the existing
  `workflow_integration_test.py:2469–2475` set-iteration test covers the
  frozensets generically but proves nothing about this specific label.

**Implementation evidence grouped by sub-obligation** (`GPT-FUP-R6-O02`,
round-6 manual external plan review): CP1 bundles four distinct
sub-obligations beyond its own headline fix — the new
`"step-5-stage-and-pin"` guard-step label, the `approve-review.md` step
6.4 trailer final-paragraph sentence (REQ-14), and the baseline
housekeeping (grandfathered trailer-lookalike entry, this plan document's
own artifacts exclusion — REQ-15) — on top of the actual fifth-member
choreography regression itself. When CP1 is implemented, its own
completion evidence must record these four sub-obligations separately
(distinct bullets or commit-scoped notes, not one undifferentiated diff
summary) so a later reviewer can tell which sub-obligation any given hunk
belongs to. A documentation/evidence-organization instruction only — no
scope change.

### CP2 — `/review-implementation` canonical feedback writeback + plan-conformance read

Mirror `/review-plan`'s operator-facing writeback shape into
`.claude/commands/review-implementation.md`, without importing any
plan-specific ledger/state-transition machinery:

- **Step 3's read list**: add `<bundle_dir>/PLAN.md` and the item's own
  `plan_path` (item 4), alongside the existing eight bundle artifacts
  (**corrected from "nine," `LPR-R7-O03`**).
- **Step 5's search list**: add a plan-conformance arm mirroring
  `/review-plan` step 6's "independently verify every finding... against
  the actual repository state" instruction — checking the implementation
  against what the approved plan actually specified, not just against the
  bundle's own self-reported disposition.
- **Second `assert_bundle_not_rejected` call**: today's single call (end
  of step 4, before reporting) stays; add a second call immediately before
  the write, mirroring `/review-plan` step 8's "second of two, under this
  step's own mutation guard" pattern — a withdrawal landing between step 4
  and the write must still be caught. **This is a genuine `WFR-67`
  classification change, not just a count bump** (`LPR-R1-I01`):
  `/review-implementation` moves from the "once" report-only consumer
  group to the "twice" mutation-guarded group `apply-plan-review`,
  `approve-review`, `record-manual-plan-review`, and `review-plan` already
  occupy, for the same reason those four are there — a real write now
  follows the guard. `REVIEW_SUBJECT_ROSTER`'s `bundle` value and
  `review-implementation.md`'s `review-subject: bundle` /
  `state_writer: false` frontmatter are unchanged (`state_writer` tracks
  `WORKFLOW_STATE.json` writes specifically, and this checkpoint adds
  none) — only the `assert_bundle_not_rejected` call count changes.
- **The write itself**: after composing the report (existing step 6's
  report *structure* unchanged — binding fields freshly recomputed, no new
  `Reviewer role:` field, since implementation-stage review has never used
  one and `/apply-implementation-review` never checks one; step 6's own
  provenance clause is not unchanged — see "`review-implementation.md`'s
  own step 6/step 7 provenance text" below), resolve `<feedback_dir>` via
  the existing, **unmodified** `resolve_feedback_dir(repo_root,
  work_item_id)`, read whatever `REVIEW_FEEDBACK.md` already sits there (if
  any), and call the new guard
  `workflow_fingerprint.assert_feedback_not_owned_by_other_work_item`
  (below) against it — only once that guard, and the second
  `assert_bundle_not_rejected` call above, both pass does the write land,
  unconditionally, at `<feedback_dir>/REVIEW_FEEDBACK.md`, instead of only
  printing it. **On a refusal from `assert_feedback_not_owned_by_other_work_item`,
  the composed report is still printed in full, exactly as today's step 7
  does, with the refusal stated alongside it; on a refusal from the second
  `assert_bundle_not_rejected` call, the report is suppressed entirely,
  exactly as the existing step 4 call already does — this checkpoint adds a
  guarded write, it does not change the withdrawal invariant that call
  already enforces** — see the "Refusal behavior and operator recovery"
  bullet below for the full specification (**narrowed this revision**,
  `LPR-R9-B01`, round-9 local plan review, correcting round-8's
  `LPR-R8-I01`, which had generalized this to either guard).
- **`assert_feedback_not_owned_by_other_work_item`, new function**
  (`scripts/workflow_fingerprint.py`, sibling to
  `parse_review_feedback_binding_fields`/`assert_feedback_matches_bundle`,
  immediately below them — **replaces revision 7's
  `ensure_scoped_feedback_dir`,** `LPR-R7-B01`, round-7 local plan review):
  `assert_feedback_not_owned_by_other_work_item(existing_content: str |
  None, *, work_item_id: str) -> None`. If `existing_content` is `None` (no
  file at the resolved path yet), returns immediately. Otherwise parses it
  with the already-existing `parse_review_feedback_binding_fields` and,
  only when the parsed `work_item` field is present and differs from
  `work_item_id`, raises a new `FeedbackOwnedByOtherWorkItemError`
  (`scripts/workflow_fingerprint.py`, beside `MissingFeedbackBindingFieldError`/
  `FeedbackBundleMismatchError`, `LPR-R8-O01`, round-8 local plan review)
  naming both the existing file's `work_item` value and the current
  `work_item_id` — never silently overwriting, and never touching
  `resolve_feedback_dir` or any resolver. A missing/unparsed `work_item`
  field (a hand-authored file, or one predating the binding-field
  convention) is treated as unowned and does not block the write, matching
  how a same-work-item overwrite already behaves today via `/review-plan`
  step 8's guard-then-overwrite pattern. This delivers `GPT-FUP-R6-I01`'s
  own guarantee — "one work item's `/review-implementation` run can never
  destroy another work item's pending feedback" — by refusing instead of
  relocating: `resolve_feedback_dir`'s answer for every command and every
  work item is completely unchanged, and no conflict with
  `OPUS-R28-005` (`docs/ai-workflow/WORKFLOW_V2_PLAN.md:4387` — the
  accepted finding that `.ai-review/feedback/` stays flat and
  stage-agnostic for every stage but plan) is created.
- **Refusal behavior and operator recovery, specified** (**narrowed and
  corrected this revision**, `LPR-R9-B01`/`LPR-R9-I01`, round-9 local plan
  review, revising round-8's `LPR-R8-I01`): the two pre-write guards —
  `assert_feedback_not_owned_by_other_work_item` above, and the second
  `assert_bundle_not_rejected` call — do not behave identically on refusal,
  and neither discards the review the operator just paid for.
  **Report-printing, per guard** (`LPR-R9-B01`, correcting round-8's
  generalization to "either pre-write guard"): a refusal from
  `assert_feedback_not_owned_by_other_work_item` still prints the fully
  composed report in this turn's response, exactly as today's step 7
  already does, and states the refusal alongside it, naming both work item
  ids; the operator loses only the write, not the review. A refusal from
  the second `assert_bundle_not_rejected` call instead suppresses the
  report entirely, exactly as the existing step 4 call already does — this
  checkpoint's new write does not change the withdrawal invariant that call
  already enforces; generalizing report-printing to that call too would
  have handed the operator a complete, copy-pasteable advisory report for a
  bundle that has already been withdrawn, reversing `WFR-67`'s purpose for
  this command.

  **Recovery, keyed on A reaching a terminal phase** (**corrected this
  revision**, `LPR-R10-B01`, round-10 local plan review, replacing
  revision 9's consumption-keyed signal, `LPR-R9-I01`, which itself
  corrected round-8's wait-then-re-run branch that did not actually clear
  the guard; **refined further**, `LPR-R11-I01`/`LPR-R11-I02`/`LPR-R11-O01`,
  round-11 local plan review, correcting a phase-count/enumeration
  mismatch, adding a dormant-tracked-item exit, and softening an
  over-strong justification — none of which touch the terminality keying
  itself): recovery from an ownership refusal is stated explicitly
  rather than left to the operator's own inference from what CP2 rejected.
  **Hand-creating `.ai-review/<work_item_id>/feedback/` is not an endorsed
  remedy** — doing so would reproduce, by hand, exactly the
  silent-shadowing hazard `LPR-R7-B01` rejected `ensure_scoped_feedback_dir`
  for. When the blocking file's own `Work item:` field names a work item A
  tracked in `docs/ai-workflow/WORKFLOW_STATE.json` and *live* — its
  `phase` still advancing toward `MILESTONE_COMPLETE` on some scheduled
  cause, not dormant (`LPR-R12-I01`, round-12 local plan review, narrowing
  this rule so the dormant branch below reads as a disjoint case rather
  than a contradiction) — the operator must leave the file in place and
  wait; re-running `/review-implementation` before A reaches a terminal
  phase changes nothing, since nothing in this repository deletes or
  relocates `REVIEW_FEEDBACK.md` as a side effect of A's own review
  cycle. **A's feedback is live, in one of two senses, for
  the entire span between the round that wrote it and A's own terminal
  phase, `MILESTONE_COMPLETE`** (correcting revision 9's two-member
  exception list, which inverted the guard's purpose by implication — see
  below). The phases named below are the *reasons* that span is unsafe to
  delete in, not a closed set with a cardinality (`LPR-R11-I01`, round-11
  local plan review, correcting revision 11's own mismatched "five phases"
  label against a seven-phase enumeration): three where the file is
  unconsumed and waiting, four more where it is consumed but still read by
  a named later command. It is *unconsumed* — written and waiting to be
  acted on — while A's `phase` is `AWAITING_EXTERNAL_PLAN_REVIEW` (`"1"`
  items), `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, or
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`; it is *consumed but still
  read* while A's `phase` is `REVISING_PLAN` — `MILESTONE_WORKFLOW.md`'s
  own entry condition for that phase is exactly "`REVIEW_FEEDBACK.md`
  exists", a section shared unmodified by both governing-version branches —
  `APPLYING_REVIEW_FEEDBACK` (entered only by `/apply-implementation-review`
  step 0's own `enter_applying_review_feedback`,
  `scripts/workflow_state.py:9087–9107`), or either approval phase,
  `AWAITING_PLAN_APPROVAL`/`AWAITING_TECHNICAL_APPROVAL` (`approve-review.md`
  step 1 reads `<feedback_dir>/REVIEW_FEEDBACK.md` for the most recently
  reviewed round's status and bundle ID at those phases too, and
  `resolve_approval_basis`'s own docstring,
  `scripts/workflow_state.py:8809–8815`, states plainly that
  `latest_round_status` is whatever the caller read from
  `REVIEW_FEEDBACK.md` *this turn* — so a deleted file forfeits A's
  `EXTERNAL_APPROVE` basis there). **This is why the endorsement below is
  keyed on terminality, not consumption**: consumption alone is not
  sufficient for deletion to be non-destructive, only necessary.
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is not an edge case here — it
  is the exact phase this checkpoint's own new write leaves A in
  immediately after `/review-implementation A` runs (CP2 adds no phase
  transition, `state_writer: false`, stated twice above), so the recovery
  must never call that phase spent: doing so would endorse deleting the
  artifact this checkpoint's own write just created, precisely the
  destruction `GPT-FUP-R6-I01` opened this thread to prevent. Only once a
  live A's `phase` independently reaches `MILESTONE_COMPLETE` — the phase
  at which this plan is willing to guarantee no later command reads
  `REVIEW_FEEDBACK.md` for A, not merely the only phase at which one
  happens to be absent (`LPR-R11-O01`, round-11 local plan review) — may
  the operator delete the blocking file by hand and re-run; **deletion, not
  the re-run by
  itself, is the step that clears the refusal**. Two work items both parked
  at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` against the same flat path
  therefore genuinely block each other until one of them reaches
  `MILESTONE_COMPLETE`, which can be a long wait — the recovery text states
  that plainly rather than implying a short one. The two work items' review
  rounds are not meant to be simultaneously live against the same flat
  path, the same discipline `/review-plan`'s own inherited flat-fallback
  exposure already requires today, undisturbed by this checkpoint. When the
  blocking file's own `Work item:` value names no entry in
  `WORKFLOW_STATE.json` at all, there is no `phase` to consult and the file
  cannot be a live tracked round; the operator judges it by hand from the
  file's own `Reviewed bundle ID:`/`Reviewed base commit:` fields and may
  delete it if it is leftover (`LPR-R10-O01`, round-10 local plan review).
  When the blocking file's own `Work item:` value instead names an entry in
  `WORKFLOW_STATE.json` that is tracked but dormant — its `phase` not
  advancing toward `MILESTONE_COMPLETE` on any scheduled cause — the phase
  signal is not a wait either: `LEGACY_READY` is named explicitly, since
  `scripts/workflow_state.py:294–295` documents it as "dormant, not
  terminal" and `docs/ai-workflow/WORKFLOW_STATE.json` holds `milestone-8`
  there today with no scheduled transition an operator blocked by the file
  can cause; the operator judges the file by hand from its own `Reviewed
  bundle ID:`/`Reviewed base commit:` fields, exactly as the untracked
  branch above, and may delete it if it is leftover (`LPR-R11-I02`,
  round-11 local plan review). The guard itself performs no such phase or
  terminality check by design:
  it stays the pure, `WORKFLOW_STATE.json`-independent function
  `assert_feedback_not_owned_by_other_work_item` already is, not a
  state-aware one, so recognizing a spent file is a manual judgment call
  the operator makes by consulting `WORKFLOW_STATE.json` directly, not
  something this checkpoint automates.
- **Safe replacement semantics**: reuse `/review-plan`'s existing design —
  the freshness guarantee comes from the fresh `bundle_id`/
  `review_content_id` recompute and `assert_local_generation_matches`,
  both run once at step 4, plus the second `assert_bundle_not_rejected`
  call above, which runs immediately before the write itself
  (`LPR-R4-O03`, round-4 local plan review: only that second call is
  genuinely immediate — the first two are recomputed once, before the
  whole of step 5's independent verification, exactly as inherited from
  `/review-plan`'s own step 5/step 8 split; this is not a defect, since
  `assert_feedback_matches_bundle` fails closed downstream against
  whatever bundle `/approve-review implementation` is actually approving,
  but the two checks are not re-run a second time immediately before the
  write, and REQ-4's own "pre-write and immediate-pre-write" phrasing
  should not be read as requiring a second recompute this checkpoint does
  not intend), not from inspecting the old file's freshness.
  `assert_feedback_not_owned_by_other_work_item` above is a distinct,
  additional guard — checking *ownership*, not freshness — and runs
  alongside the second `assert_bundle_not_rejected` call, immediately
  before the write. Once every guard passes, the write unconditionally
  overwrites whatever same-work-item feedback (if any) currently sits at
  the resolved path — the same guard-then-overwrite shape `/review-plan`
  step 8 uses. No new staleness-detection subsystem is introduced; none
  exists today and none is needed to satisfy this requirement. The write
  happens only after every guard passes, so the four negative paths below
  ("stale bundle", "`REJECTED` bundle", "wrong-phase", "owned by another
  work item") still create nothing on disk.
  **Cross-work-item exposure, resolved** (`GPT-FUP-R6-I01`, round-6 manual
  external plan review, revising `LPR-R1-I02`'s round-1 "decided
  explicitly, accepted" disposition — **and revising revision 7's own
  resolution**, `LPR-R7-B01`, round-7 local plan review):
  `resolve_feedback_dir`'s scoped-else-flat rule means the file an
  automatic write overwrites may belong to a *different* work item
  entirely whenever neither item has a scoped
  `.ai-review/<work_item_id>/feedback/` directory yet — concretely
  demonstrated live during this very item's own execution, where
  `workflow-v2-3`'s own terminal-approval `REVIEW_FEEDBACK.md` sat at the
  flat path this item's round-1 `/review-plan` bundle then wrote over.
  Today `/review-implementation` writes nothing, so it cannot cause this;
  after this checkpoint it would, automatically, with no operator gate in
  between — materially riskier than `/review-plan`'s own inherited
  exposure, which at least only lands when an operator chooses to run
  `/review-plan` for a given work item.

  Revision 7 tried to close this by making `/review-implementation`'s own
  write target work-item scoped via a new `ensure_scoped_feedback_dir`
  call, ahead of the write. Round-7 local plan review (`LPR-R7-B01`)
  rejected that fix: nothing in this repository creates a scoped feedback
  directory today (`scripts/prepare-ai-review.sh` never mentions
  `feedback`, and `relocate_flat_bundle_to_scoped_layout`'s own docstring
  states it "Never touches `.ai-review/feedback/` ... `OPUS-R28-005`"), so
  `ensure_scoped_feedback_dir` would have been the *first and only*
  mechanism ever scoping a work item's feedback directory — silently
  changing `resolve_feedback_dir`'s answer, from that point on, for every
  later command and every later read against that work item, including
  the human operator's own paste target:
  `docs/ai-workflow/MILESTONE_WORKFLOW.md`'s
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` **Exit** condition
  (`:217–218`), its "Feedback file locations" section (`:449–453`), its
  `AWAITING_EXTERNAL_PLAN_REVIEW` entry (`:51–53`), and
  `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location" section
  (`:56`, `:71`) all instruct a human reviewer to place feedback at the
  flat `.ai-review/feedback/REVIEW_FEEDBACK.md` — none of which revision
  7's edit inventory updated. The result would have been a *silent
  shadowing* hazard, not a fix: an operator who ran `/review-implementation`
  once, then later pasted a real external reviewer's verdict at the
  (now-stale, for that work item) flat path exactly as every one of those
  documents still instructs, would have their paste permanently ignored by
  every command that resolves feedback for that work item from then on —
  with no error, no missing file, and binding fields that still parse and
  validate cleanly against the *wrong* file. `resolve_rejected_marker_path`
  was cited as this fix's precedent ("the same paired-resolver
  relationship `resolve_bundle_dir` and `resolve_rejected_marker_path`
  already share") — that citation was inaccurate:
  `resolve_rejected_marker_path` guarantees agreement *by derivation* (it
  returns `resolve_bundle_dir(...).parent / "REJECTED"` and cannot
  disagree), while `ensure_scoped_feedback_dir` would have guaranteed
  agreement by *mutating the filesystem so `resolve_feedback_dir`'s answer
  changes* — a materially different, riskier mechanism.

  **Revised decision, this revision** (`LPR-R7-B01`, option (a), "smaller
  than what revision 7 proposes"): leave `resolve_feedback_dir` and every
  path it resolves completely alone — the write lands wherever
  `resolve_feedback_dir` already resolves today, exactly as every
  operator-facing document already instructs, with no directory-ensure
  step and no change in resolution for any other command or work item,
  ever. `GPT-FUP-R6-I01`'s actual guarantee is delivered by
  `assert_feedback_not_owned_by_other_work_item` above instead: the write
  *refuses* rather than *relocates* when the resolved path already holds a
  different work item's feedback. This is smaller than revision 7's fix
  (one guard function, no new directory-creation side effect, no
  operator-doc changes required, no `OPUS-R28-005` conflict) and does not
  reintroduce the general feedback-directory redesign round 6 explicitly
  told this plan not to undertake — `/review-plan`'s own inherited
  flat-fallback exposure is, as before, explicitly **not** touched by this
  checkpoint. Because `resolve_feedback_dir` never changes its answer for
  a given work item as a side effect of this checkpoint, there is no "once
  scoped, always scoped, reached one point earlier" property to state
  (that claim, in revision 7's text, described a consequence of
  `ensure_scoped_feedback_dir` that no longer exists) and no read/write
  path asymmetry to correct: `/review-implementation` step 3's own read of
  "prior `REVIEW_FEEDBACK.md` for continuity across rounds" and its own
  later write always resolve the identical path, for every run, because
  neither one ever causes `resolve_feedback_dir`'s answer to change.
- **Every other existing prohibition preserved verbatim**: no
  `WORKFLOW_STATE.json` write, no `phase` transition, no approval, no
  source/test/plan/registry/mapping/artifacts-declaration/bundle edits, no
  auto-continuation into another command. `/apply-implementation-review`
  remains the sole authoritative path that *acts on* the file this command
  now writes; `/approve-review implementation` remains the sole approval
  gate. Both stay completely unmodified by this checkpoint.

Doc sync (incidental to this checkpoint, since both docs describe the
command's own behavior): `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Local
reviewer commands" section currently describes `/review-implementation`
and `/review-functional` with one shared "writes nothing" sentence — split
it so each command's own (now different) behavior is stated precisely,
with `/review-functional`'s continuing to read "unchanged" explicitly.
`docs/ai-workflow/MILESTONE_WORKFLOW.md`'s "Hard gates summary" sentence
("neither writes state nor advances `phase`") needs the same precision:
`/review-implementation` now writes a review artifact
(`<feedback_dir>/REVIEW_FEEDBACK.md`) but still writes no *workflow state*
(`WORKFLOW_STATE.json`) and advances no `phase` — still not a seventh
gate, for the same reason as before, stated without overclaiming "writes
nothing."

**`MILESTONE_WORKFLOW.md`'s own third occurrence, added to the inventory**
(`LPR-R4-I01`, round-4 local plan review): the same file's
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` state's own "Allowed actions"
block (`docs/ai-workflow/MILESTONE_WORKFLOW.md:209–213`) states, of
`/review-implementation`, "it writes nothing and never advances this
state, so it adds no new gate" — the same false-after-CP2 claim as the
"Hard gates summary" sentence above, but in the section an operator
actually consults to decide what they may run while parked at this gate,
which makes it the more operator-visible of the two. It needs the same
precision: `/review-implementation` now writes a review artifact but still
writes no `WORKFLOW_STATE.json` and advances no `phase`, so it remains
non-gating — just no longer "writes nothing." `/review-functional`'s own
equivalent block under `AWAITING_FUNCTIONAL_REVIEW`
(`docs/ai-workflow/MILESTONE_WORKFLOW.md:260–263`) states the identical
sentence and stays true and untouched, since `/review-functional` itself
is unchanged by this checkpoint (REQ-7) — no per-command split is needed
there. No new doc-assertion test is added for either
`MILESTONE_WORKFLOW.md` sentence: nothing in the test suite pins any
sentence of that file's prose today, and adding the first one is declined
for this checkpoint's minimal-change scope, the same disposition CP1
gives `"step-5-declaration-pin"`/`"step-6.2-stage-ordinary"` above — the
inventory entry itself, verified by CP4's cross-check below, is what
prevents this sentence from going stale unnoticed.

**`MILESTONE_WORKFLOW.md`'s fourth occurrence, added to the inventory**
(`LPR-R8-I02`, round-8 local plan review): the same file's "Feedback file
locations" section (`docs/ai-workflow/MILESTONE_WORKFLOW.md:448–454`)
states "Both are read, never written, by Claude" of
`.ai-review/feedback/REVIEW_FEEDBACK.md` and
`.ai-review/feedback/FUNCTIONAL_REVIEW.md` together — the same class of
write-set overclaim as the "Hard gates summary" sentence and the
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` clause above, and it is the
section an operator consults specifically to learn what happens to
`REVIEW_FEEDBACK.md`. Unlike those two, the sentence is already false at
`base_commit` — `/review-plan` step 8 writes
`<feedback_dir>/REVIEW_FEEDBACK.md` today, a pre-existing inaccuracy this
milestone did not introduce — but CP2 makes the claim false for a second,
independent reason too, and before this revision the sentence was cited
only inside the narration of why revision 7's fix was rejected (plan line
~730, a path-location argument about the flat path, not a disposition of
this sentence's own truth value), so nothing in this milestone would have
looked at it: CP4's coherence cross-check below enumerated exactly three
sites before this revision. Correct it minimally, splitting by artifact
the same way the other two sentences were split by command:
`REVIEW_FEEDBACK.md` is written by `/review-plan` (pre-existing) and,
after this checkpoint, by `/review-implementation` too;
`FUNCTIONAL_REVIEW.md` stays read-only, written by nothing this
repository's commands touch. No new doc-assertion test is added, the same
disposition the third occurrence above already received, for the same
reason: nothing in the test suite pins this file's prose today, and the
inventory entry, verified by CP4's now-four-site cross-check, is what
prevents the sentence from going stale unnoticed.

**`docs/ACTIVE_MILESTONE.md` and `workflow-v2-3-ledger.md`'s own
`/review-implementation` mentions, dispositioned** (`LPR-R5-O04`, round-5
local plan review, closing the enumeration round 4 asked for): the
remaining two file classes naming `/review-implementation` are
`docs/ACTIVE_MILESTONE.md` (eight mentions, including "Neither command
writes any state" at 792–807) and
`docs/ai-workflow/requirements/workflow-v2-3-ledger.md` (five mentions,
including "report-only" language at line 61). Both are `excluded` at both
stages in this item's own artifacts declaration, and both are knowingly
left stale rather than updated: each is a completed work item's own
historical record of what was true when it was written (`workflow-v2-3`'s
functional-review narrative and its own execution ledger, respectively),
not live documentation this checkpoint maintains — the same
knowingly-left-stale-historical-record disposition item 2 #8 and item 3
#7 already carry above (see "Disposition of item 2 #8 and item 3 #7"),
not silently dropped.

**Second `REVIEW_PROTOCOL.md` sentence, named explicitly** (`LPR-R2-B01`,
round-2 local plan review): the same "Local reviewer commands" paragraph
contains a second, separate sentence immediately after the shared
"writes nothing" one — "A user may hand-copy either command's printed
report into `<feedback_dir>/REVIEW_FEEDBACK.md` themselves, verbatim or
after obtaining a further external reviewer's own separate pass, if they
choose to treat it as the authoritative external round — that choice is
always the user's, never automatic." This states a *provenance* property
(who installs the authoritative round), not a write-set property, and
CP2 makes it false for `/review-implementation` specifically: after this
checkpoint, `/review-implementation` installs its own output as the
current `<feedback_dir>/REVIEW_FEEDBACK.md` unconditionally once its
guards pass, with no operator choice in between. This sentence must be
split by command, same as the "writes nothing" sentence above:
`/review-functional` keeps "that choice is always the user's, never
automatic" verbatim (its behavior is genuinely unchanged);
`/review-implementation`'s clause is replaced with a statement of its new
provenance reality — its own write *is* the authoritative round the
moment it lands, with no separate operator installation step, and the
"never automatic" property no longer holds for it.

**`review-implementation.md`'s own step 6/step 7 provenance text, added to
the edit inventory** (`LPR-R3-I02`, round-3 local plan review): the same
provenance statement `REVIEW_PROTOCOL.md` states also appears twice more,
inside the command file CP2 itself edits, and both instances become false
the same way once CP2 lands:

- **Step 6** (`review-implementation.md:168–172`): "...so that if the
  user chooses to hand-copy this report into
  `<feedback_dir>/REVIEW_FEEDBACK.md` as the authoritative external
  round, it already satisfies `parse_review_feedback_binding_fields`/
  `assert_feedback_matches_bundle` without further editing." After CP2,
  satisfying the binding parsers is not a convenience for a hypothetical
  hand-copy — it is a hard precondition of the command's own write. This
  clause must be reworded accordingly.
- **Step 7** (`review-implementation.md:180–186`): "`<feedback_dir>/REVIEW_FEEDBACK.md`
  ... and every other repository file are untouched. If the user wants
  this opinion to become the authoritative round, they place it
  (verbatim, or after obtaining a further external reviewer's own
  separate pass) at `<feedback_dir>/REVIEW_FEEDBACK.md` themselves." This
  entire clause is false the moment CP2 lands and must be replaced with a
  statement that the command's own write *is* the authoritative round.

Neither occurrence is reached by any existing test — CP2's conformance-
surface obligation below (golden hash,
`test_states_the_report_only_constraint`/`test_states_it_writes_nothing`,
`EXPECTED_ASSERTION_COUNT`) covers the header sentence (lines 16–17) and
the "**Report only — writes nothing.**" literal (line 177), not step 6's
clause or step 7's body — so this must be tracked as its own inventory
item, not assumed covered by the mechanisms already listed. `/review-functional`'s
equivalent language in its own command file stays true and untouched — it
is not edited by this checkpoint. (The "existing step 6" reference in
"The write itself" bullet above means the report's *structure* only — see
that bullet's own correction.)

**Frontmatter `description:` line, added to the edit inventory**
(`LPR-R4-I02`, round-4 local plan review): `review-implementation.md`'s
own YAML frontmatter (line 2) reads "...Report-only -- never writes
state, never approves, never applies findings." This is the sentence the
harness surfaces in the command listing — the one statement about this
command an operator reads without opening the file — and "Report-only" is
false the moment CP2 lands, the same failure mode as step 6's and step
7's clauses above but on a more operator-visible surface. It is added to
CP2's edit inventory alongside step 3, step 5, the second guard call, the
write, step 6's clause, and step 7's body, narrowed the same way the two
rewritten report-only assertions are: writes
`<feedback_dir>/REVIEW_FEEDBACK.md`; never writes `WORKFLOW_STATE.json`,
never approves, never advances `phase`. The frontmatter's other two
fields are verified-correct-and-unchanged rather than merely unmentioned:
`state_writer: false` stays correct because that field tracks
`WORKFLOW_STATE.json` writes specifically and this checkpoint adds none
(the same argument the `WFR-67` bullet above already makes), and
`review-subject: bundle` stays correct because the review subject is
still the bundle either way — neither field needs editing.

**CP2's `EXTERNAL_APPROVE`-provenance decision, recorded explicitly**
(`LPR-R2-B01`): after this checkpoint, a locally-authored
`/review-implementation` round can reach `/approve-review implementation`'s
`EXTERNAL_APPROVE` basis with no on-disk marker distinguishing it from a
round a human reviewer actually performed — `resolve_approval_basis`
only checks `latest_round_status == "APPROVE"` and a matching
`feedback_bundle_id`, neither of which encodes authorship. **Decision:
accept this exposure, do not add a `Reviewer role:` field to
`/review-implementation`'s output.** Reasons, not merely "nothing checks
it today":
- What actually preserves the gate is unrelated to file provenance:
  `/approve-review`'s own `disable-model-invocation: true` guard and its
  specificity-checked literal `user_confirmation` text remain the sole
  mechanism deciding whether a human ever ratifies the approval, exactly
  as before this checkpoint — a `Reviewer role:` marker on
  `REVIEW_FEEDBACK.md` would be informational only and could not itself
  gate anything without new validation code, which REQ-11 forbids adding
  as new lifecycle/ledger machinery.
- The two literal values `WORKFLOW_V2_3_FOLLOWUPS.md` item 3 explicitly
  bars introducing as new active lifecycle semantics —
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` / `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
  — are precisely the vocabulary a `Reviewer role:` marker on this
  command's output would most naturally use. Even a purely informational,
  unvalidated marker using that exact shape risks being read by a future
  operator or a Workflow Manager/Bootstrapper design pass as the start of
  a real local-vs-external implementation-review distinction, which item
  3's own "Important scope boundary" and this plan's REQ-11 both defer.
  Declining the marker keeps that vocabulary introduced nowhere in this
  milestone, not even as inert text.
- The usability cost is real but bounded: an operator who wants to know
  whether a given `REVIEW_FEEDBACK.md` came from `/review-implementation`
  or a human can already tell from context (whether the operator
  themselves pasted it) and from the surrounding session/chat transcript;
  nothing about `/apply-implementation-review`'s or `/approve-review`'s
  own behavior depends on knowing which.

This decision is traceable through REQ-4 below.

Housekeeping: `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md` (git-added and
tracked for the first time as part of this checkpoint, since it is this
item's own source-of-requirements document) gets item 4's `Status` field,
and item 2's required follow-up items 1–7 and 9, updated to record this
checkpoint's closure of them. Item 2's own required follow-up #8 ("Update
the operator reference and lifecycle diagram so the normal
`/review-implementation` path no longer contains a manual copy/paste
step") is recorded as explicitly deferred, not closed — see `LPR-R2-I01`'s
disposition in CP4 below.

**Conformance-surface obligation** (`LPR-R1-B02`): three independent
pinned-content mechanisms fail as a direct, deterministic consequence of
this checkpoint's own described edit, and each must be updated in the
same commit:

1. **Golden hash.** `_GOLDEN_COMMAND_FILE_SHA256["review-implementation.md"]`
   (`workflow_integration_test.py`) must move to the new post-edit hash,
   with a rationale comment, matching the surrounding dictionary's
   convention.
2. **Exact-literal report-only assertions.**
   `TestReviewImplementationCommandStaticConformance.test_states_the_report_only_constraint`
   and `.test_states_it_writes_nothing` both assert literal sentences this
   checkpoint makes false by design (the command no longer "never writes
   `<feedback_dir>/REVIEW_FEEDBACK.md`" and is no longer "Report only —
   writes nothing."). Both must be rewritten to the new, narrower
   invariant: writes `REVIEW_FEEDBACK.md`; still never writes
   `WORKFLOW_STATE.json`, never approves, never advances `phase` — the
   same precision correction this checkpoint already applies to
   `REVIEW_PROTOCOL.md`/`MILESTONE_WORKFLOW.md`'s prose, applied here to
   the tests that pin the command file's own text.
3. **`WFR-67` shared-assertion count.** `workflow_state_demo_test.py`'s
   `TestReviewSubjectDeclarationsLive.EXPECTED_ASSERTION_COUNT["...review-implementation.md"]`
   must move from `1` to `2`, and the explanatory comment immediately
   above the table (the "twice" vs. "once" rationale, `LPR-R1-I01`) must
   be updated so its stated reasoning still matches its own contents —
   `/review-implementation` no longer belongs to the "once" report-only
   group the comment currently places it in.

**Sequencing** (closing note to the obligation above): because item 3's
table lives in `workflow_state_demo_test.py`, and this plan's own CP4 is
the only checkpoint that runs the real-repository demo suites, a naive
reading would leave this breakage undiscovered for two checkpoints. **CP2
must itself run `workflow_state_demo_test.py`** (in addition to the
ordinary suite every checkpoint already runs) before considering itself
complete, so the count-table update above is verified where it is
introduced, not two checkpoints later.

Regression coverage:

- **Binding round-trip**: a freshly written `REVIEW_FEEDBACK.md` parses
  and validates cleanly through `parse_review_feedback_binding_fields` /
  `assert_feedback_matches_bundle`.
- **Replacement-only-on-success**: stale prior feedback (from an older
  bundle) is left in place if the review's own guards fail partway through
  — replacement happens only after a full, successful review of the
  *current* bundle.
- **Two-work-item feedback isolation, refusal-based** (`GPT-FUP-R6-I01`,
  **revised this revision**, `LPR-R7-B01`): work item A has a pending
  `REVIEW_FEEDBACK.md` at the path `resolve_feedback_dir(repo_root, "A")`
  currently resolves (flat, in this fixture); driving
  `/review-implementation`'s write path for a *different* work item B
  against that *same* resolved path (the fixture arranges
  `resolve_feedback_dir(repo_root, "B")` to resolve identically,
  reproducing the real flat-fallback collision) proves the write
  **refuses** via `FeedbackOwnedByOtherWorkItemError`, naming both work
  item ids, and that A's file is byte-identical before and after —
  replacing revision 7's `ensure_scoped_feedback_dir` isolation/idempotency
  pair. A companion case proves a *same-work-item* overwrite (the existing
  file's `Work item:` line already names the work item under review, or
  the path is empty) still succeeds normally, and that a pre-existing file
  with no parseable `Work item:` line (predating the binding-field
  convention) does not block the write. **Extended this revision**
  (`LPR-R8-I01`, round-8 local plan review): the refusal case additionally
  asserts that the refused run creates no
  `.ai-review/<work_item_id>/feedback/` directory as a side effect — the
  observation that distinguishes this revision's refusal-based mechanism
  from revision 7's rejected `ensure_scoped_feedback_dir` side effect, and
  pins it against a future implementer reintroducing an
  "ensure-then-write" shortcut when the refusal proves inconvenient in
  practice.
- **Three independent negative paths write nothing**: a stale bundle
  (worktree/HEAD mismatch via `assert_local_generation_matches`), a
  `REJECTED` bundle, and a wrong-phase work item.
- **Plan-conformance read is actually documented**: a doc-assertion test
  confirming `review-implementation.md`'s read list names `PLAN.md`/
  `plan_path`, following the existing precedent for asserting a command
  file's own literal text: `TestReviewImplementationCommandStaticConformance`
  (`scripts/workflow_integration_test.py`), which already `setUp`s
  `self.text = _command_text("review-implementation.md")` — the new
  assertion belongs as another method on that class, not a new fixture.
  (**Correction, `LPR-R2-O02`**: revision 2 pointed at
  `scripts/workflow_integration_test.py:1342` as the precedent; that line
  asserts a substring of `docs/ai-workflow/WORKFLOW_V2_PLAN.md` inside a
  different test, not of any `.claude/commands/*.md` file's own text, and
  is not the right pattern to mirror here.)
- **Step 6/step 7 provenance text no longer reads as manual installation**
  (`LPR-R3-I02`): a further method on
  `TestReviewImplementationCommandStaticConformance` confirming
  `review-implementation.md` no longer states that the operator installs
  the authoritative round by hand — the same class as the two rewritten
  report-only assertions above, extended to the two sentences neither of
  those reaches.
- **Frontmatter `description:` no longer reads "Report-only"**
  (`LPR-R4-I02`): the same doc-assertion class, one more `assertNotIn` on
  `TestReviewImplementationCommandStaticConformance` — since it is the
  same file and the same class, this extends the method above rather than
  adding a new fixture.
- **Write step names the ownership guard** (`GPT-FUP-R6-I01`,
  **revised this revision**, `LPR-R7-B01`): a further method on
  `TestReviewImplementationCommandStaticConformance` confirming
  `review-implementation.md`'s write step names
  `assert_feedback_not_owned_by_other_work_item` and states it runs
  immediately before the write, against the unmodified
  `resolve_feedback_dir(repo_root, work_item_id)` path — the same
  doc-assertion class as the bullets above, proving the command's own
  prose actually instructs the fix, not only that the underlying Python
  function exists and behaves correctly in isolation.
- **Refusal-path prose states report-printing and recovery, narrowed
  per guard and keyed on terminality** (**corrected this revision**,
  `LPR-R10-B01`, round-10 local plan review, correcting round-9's
  consumption-keyed assertion, `LPR-R9-B01`/`LPR-R9-I01`, which itself
  narrowed round-8's `LPR-R8-I01`): a further method on
  `TestReviewImplementationCommandStaticConformance` confirming
  `review-implementation.md`'s own prose states the report-printing
  behavior **per guard**, not as a single "either pre-write guard"
  sentence — that a refusal from `assert_feedback_not_owned_by_other_work_item`
  still prints the composed report in full, and that a refusal from the
  second `assert_bundle_not_rejected` call suppresses it instead — and
  states the operator's **terminal-phase-keyed** recovery path
  (`LPR-R10-B01`, replacing the superseded two-phase consumption wording
  `LPR-R9-I01` had added, reworded further at round 11, `LPR-R11-I01`,
  round-11 local plan review, to drop a mismatched numeric phase count,
  and qualified again this revision, `LPR-R12-I01`, round-12 local plan
  review, narrowing "A reaching `MILESTONE_COMPLETE`" to "a live A
  reaching `MILESTONE_COMPLETE`" so the pinned sentence agrees with the
  dormant branch the design paragraph already carries): that A's feedback
  is live for the entire span between the round that wrote it and A's own
  terminal phase, `MILESTONE_COMPLETE`, that
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is the phase this checkpoint's
  own write leaves A in, and that only a live A reaching
  `MILESTONE_COMPLETE` clears the refusal for deletion — including that
  hand-creating a scoped `.ai-review/<work_item_id>/feedback/` directory
  is not an endorsed remedy — the same doc-assertion class as the bullets
  above, proving the command's own text carries the specified behavior,
  not only that the underlying guard function refuses correctly in
  isolation.

### CP3 — Review-role / review-stage identifier normalization

Introduce two canonical module-level constants in
`scripts/workflow_state.py` — `LOCAL_MODEL_PLAN_REVIEW =
"LOCAL_MODEL_PLAN_REVIEW"`, `MANUAL_EXTERNAL_PLAN_REVIEW =
"MANUAL_EXTERNAL_PLAN_REVIEW"` — and repoint every write site
(`record_local_plan_review`, `record_manual_plan_review`) **and the three
read sites' lookup literals** (`plan_approval_gate_reachable`,
`validate_manual_plan_review_preconditions`, `_validate_plan_review_stages`)
to use them instead of the scattered lowercase literals (`LPR-R4-O02`,
round-4 local plan review: the two instructions only compose if the
lookup literal moves too — after `normalize_plan_review_stages` (below)
runs, its output dict's keys are `LOCAL_MODEL_PLAN_REVIEW`/
`MANUAL_EXTERNAL_PLAN_REVIEW`, so a `.get("local_model_plan_review")` call
left pointed at the old lowercase literal would miss both the legacy and
the canonical dict).

Add one small compatibility-normalization helper (e.g.
`_normalize_plan_review_stage_key(key: str) -> str`, mapping the two known
legacy lowercase tokens to their canonical uppercase form and passing any
other value through unchanged). **Applied to the stored dict's keys, not
to the lookup key** (`LPR-R1-O01`): a literal "normalize the key passed to
`.get()`" is a no-op, since `_validate_plan_review_stages` and friends do
`stages.get("local_model_plan_review")` — normalizing that literal to
`"LOCAL_MODEL_PLAN_REVIEW"` would make the lookup *miss* a legacy dict
rather than find it. The actual shape needed at each read site
(`plan_approval_gate_reachable`, `validate_manual_plan_review_preconditions`,
`_validate_plan_review_stages`) is normalizing the dict being read before
lookup. **Revised this revision** (`GPT-FUP-R6-I02`/`GPT-FUP-R6-O01`,
round-6 manual external plan review, superseding revision 6's bare
`{_normalize_plan_review_stage_key(k): v for k, v in stages.items()}`
dict-comprehension sketch): a plain per-key dict comprehension silently
picks whichever value dictionary iteration order visits *last* whenever
two raw keys normalize to the same canonical stage (e.g. a stored
`manual_external_plan_review` record alongside a stored
`MANUAL_EXTERNAL_PLAN_REVIEW` record for the same logical stage) — not a
safe compatibility rule, and it contradicts REQ-10's own "malformed/
ambiguous values still refuse cleanly" requirement. CP3 instead adds one
dedicated helper, `normalize_plan_review_stages(stages: dict) -> dict`
(`scripts/workflow_state.py`; addresses `GPT-FUP-R6-O01`'s "centralize the
compatibility and duplicate-detection rule in one place" request by
construction), used at all three read sites and by the migration function
below — never a per-call-site comprehension. It passes `review_content_id`
through unchanged (never a stage key), normalizes every other key via
`_normalize_plan_review_stage_key`, and — the collision rule
`GPT-FUP-R6-I02` requires — when two raw keys normalize to the same
canonical stage, **tolerates the duplicate silently only when both values
compare equal** (`==`; collapses to that one shared value — the case a
genuinely idempotent migration or a doubly-recorded identical write can
legitimately produce) **and otherwise raises
`AmbiguousPlanReviewStageKeyError`** (new exception,
`scripts/workflow_state.py`, beside its ten sibling plan-review-stage
exceptions (**corrected from eight, `LPR-R12-O03`, round-12 local plan
review: the family also includes `MissingLocalApprovalForManualStageError`
and `DuplicateManualStageIngestionError`, plan-review-stage exceptions by
the same standard, immediately after the eight below**) —
`PlanReviewStagesInvalidForVersionError`,
`ManualStageWithoutLocalStageError`, `StageVerdictNotApproveError`,
`UnknownPlanReviewVerdictError`,
`WrongGoverningVersionForPlanReviewStageError`,
`WrongPhaseForPlanReviewStageError`, `StaleReviewContentIdError`,
`WrongReviewerRoleError`, `MissingLocalApprovalForManualStageError`,
`DuplicateManualStageIngestionError`
(`scripts/workflow_state.py:515–571`) —
**corrected, `LPR-R7-I01`, round-7 local plan review**: revisions 1–7 all
placed this exception in `scripts/workflow_fingerprint.py` instead, a
module `grep -c "plan_review_stage" scripts/workflow_fingerprint.py`
confirms has no plan-review-stage vocabulary today; its only raiser,
`normalize_plan_review_stages`, and its only other user,
`migrate_plan_review_stage_keys`, are both defined in
`scripts/workflow_state.py`, the same module as all ten siblings above),
naming the canonical stage and both conflicting raw keys/values — never
silently choosing one by insertion order. A
historical dict built with legacy lowercase keys (concretely,
`workflow-v2-3`'s own terminal record, and any hand-authored fixture)
keeps reading correctly through this helper without being rewritten,
exactly as revision 6 already described; only the two-raw-keys-for-one-stage
collision case is new. Apply `_normalize_plan_review_stage_key` (the
lower-level, unchanged, single-key/single-value primitive
`normalize_plan_review_stages` is itself built on) directly to
`validate_manual_plan_review_preconditions`'s
`feedback_role` comparison too, but there the shape is already correct as
originally described: that comparison normalizes a single *value*
(`manual_external_plan_review` → `MANUAL_EXTERNAL_PLAN_REVIEW`), not a
dict key, so a `Reviewer role:` line pasted from an older template is
still accepted alongside the new canonical value — an explicit two-value
exact-match acceptance, not a blanket case-insensitive comparison, so a
genuinely malformed third value still refuses cleanly.

**Blast radius, stated explicitly** (`LPR-R7-O01`, round-7 local plan
review): `normalize_plan_review_stages` running inside
`_validate_plan_review_stages` (`scripts/workflow_state.py:10787–10809`)
means an ambiguous `plan_review_stages` dict on **any one** work item
makes `_validate_work_item` (`:10843–10869`) — and therefore
`validate_state`, which calls it for every work item (`:10873–10917`) —
raise for the **whole** state file, not just the offending work item;
every command, for every work item, refuses until the JSON is hand-edited.
This is intended, fail-closed behavior, matching the codebase's existing
philosophy of refusing rather than guessing on ambiguous state (the same
shape `plan_approval_step_class`'s unrecognized-label refusal and
`AmbiguousPlanReviewStageKeyError` itself both already take) — stated
here explicitly, distinct from the migration transaction's own
already-stated hand-recovery path above, so a future operator who hits it
recognizes it as designed rather than as corruption.

**Read-site inventory, completed** (`LPR-R2-O01`, round-2 local plan
review; revision 1's three-site list above named only the Python read
sites): two command-prose read sites are outside that Python-only
inventory and must be updated in the same checkpoint:

1. **`/review-plan` step 7's round computation** is a model-performed
   ledger read, not a Python one: "the round/sequence number (one more
   than the highest prior `local_model_plan_review` round on record, or
   `1` if none)". Its instruction text must gain the same
   compatibility-normalization the Python read sites get — count round
   entries under either casing of the stage key — so a round computed
   against a ledger still holding legacy-cased keys (a hand-authored
   fixture, or any future `"2.1"` item's ledger observed between its own
   `record_local_plan_review` write and a later CP3-unaware reader) finds
   the true highest prior round instead of finding nothing and recording
   round 1 again.
2. **`/record-manual-plan-review`'s exact-match expectation text**
   ("the feedback's declared role is exactly `manual_external_plan_review`")
   must be reworded to state that either the canonical
   `MANUAL_EXTERNAL_PLAN_REVIEW` or the legacy `manual_external_plan_review`
   value is accepted — otherwise the command's own documentation disagrees
   with the two-value acceptance `validate_manual_plan_review_preconditions`
   actually implements after this checkpoint.
3. **Write-site shape, documented not changed; read-side collision rule
   now closes the gap** (**revised**, `GPT-FUP-R6-I02`, round-6 manual
   external plan review): `record_manual_plan_review` writes by in-place
   key assignment
   (`work_item["plan_review_stages"]["manual_external_plan_review"] = {...}`,
   unlike `record_local_plan_review`, which replaces the whole dict). An
   item parked at `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` exactly when this
   checkpoint lands could, in principle, end up with both a legacy and a
   canonical key for the same stage in its `plan_review_stages` dict —
   revision 6 left that case resolved by read-side normalization's key
   iteration order; this revision replaces that with the explicit
   collision rule `normalize_plan_review_stages` implements: silently
   collapsed if the two records are byte-identical, or a clean
   `AmbiguousPlanReviewStageKeyError` refusal if they are not — never
   dictionary key-iteration order. Not reachable for this item today (its
   own plan-review ledger completes both stages before implementation
   begins, per "Independent inspection" below) and there is no other live
   `"2.1"` item today, so the write-site behavior itself still needs no
   change — the write sites still replace rather than merge, and that
   remains the documented behavior — but the read side (and the migration
   function below) no longer resolve a hypothetical future collision
   silently by accident; they refuse it cleanly by design, satisfying
   REQ-10's ambiguous-value requirement even though this item's own
   execution never exercises that path.
4. **`validate_manual_plan_review_preconditions`'s own docstring bullet
   and error message** (`LPR-R12-O01`, round-12 local plan review):
   the docstring's precondition bullet (`scripts/workflow_state.py:10474`,
   "the feedback's declared role is exactly `manual_external_plan_review`")
   and the `WrongReviewerRoleError` message the same function raises
   (`:10493–10496`, "expected `manual_external_plan_review`") both go
   stale the moment this item's two-value acceptance lands — the
   docstring because "exactly" stops being true, the message because the
   value it names as expected is the legacy one, not the canonical
   `MANIFEST`-casing form a post-CP3 operator would be writing. Reword
   both to state the same two-value acceptance the comparison two lines
   below already implements, mirroring item 2's command-prose rewording.
   `WrongReviewerRoleError`'s own class docstring (`:558–560`) is
   casing-agnostic and correctly needs no change.

Document the compatibility policy directly in the helper's own docstring
and in `docs/ai-workflow/MILESTONE_WORKFLOW.md`: legacy lowercase reads
remain supported so historical evidence never needs rewriting, but no code
path in this repository writes the lowercase form past this checkpoint —
enforcement is structural (only the two canonical constants are ever
written), not a runtime deprecation timer. **Added, `LPR-R5-O02` (round-5
local plan review):** state the removal/deprecation condition in REQ-10's
own checkable terms — "no live non-terminal work item holds a
legacy-cased key," true immediately after this checkpoint's migration run
completes — rather than leaving the writes-only policy above as the sole
stated condition.

Update templates and live canonical docs to the new casing:
`.claude/commands/review-plan.md` (`Reviewer role: local_model_plan_review`
→ `Reviewer role: LOCAL_MODEL_PLAN_REVIEW`), `.claude/commands/record-manual-plan-review.md`,
`docs/ai-workflow/MILESTONE_WORKFLOW.md`, `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`.
Update the ~42 combined test-fixture occurrences (`LPR-R1-O02`: 40 in
`scripts/workflow_state_test.py` plus 2 in
`scripts/workflow_integration_test.py` — one of which,
`workflow_integration_test.py:1342`'s `WORKFLOW_V2_PLAN.md` doc-substring
assertion, is itself immutable and correctly left alone, per below) to the
canonical casing for every *new* write/assertion. Explicitly leave
untouched: `docs/ai-workflow/WORKFLOW_V2_PLAN.md`,
`docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`, `docs/ai-workflow/dry-run/*.md`,
all registry/requirements JSON prose, `workflow-v2-3`'s own terminal
`plan_review_stages` block, and the untracked
`WORKFLOW_V2_1_OPERATOR_REFERENCE.md`/`diagrams/` — all per the
"Independent inspection" disposition above. **Two further occurrences,
named** (`LPR-R7-O02`, round-7 local plan review): a repo-wide grep for
the two tokens also finds `scripts/workflow_state_demo_test.py:36` and
`scripts/workflow_fingerprint_demo_test.py:51`, one occurrence each,
inside explanatory comments rather than fixture data — in the two
real-repository suites CP4 itself re-runs. Both are knowingly left as-is:
nothing breaks by leaving comment prose in the legacy casing, and
rewriting them earns no behavioral coverage. **A fifth site, named**
(`LPR-R12-O02`, round-12 local plan review): the same grep also finds five
occurrences in `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md` itself (lines
281, 405, 406, 537, 538) — a file both CP2 and CP3 open to update `Status`
fields. These are also knowingly left as-is, for a different reason than
the demo-suite comments above: this is the source-of-requirements log's
own statement of *which* tokens the milestone renames, and rewriting them
would make the requirement describe a rename that never happened.

**Live non-terminal ledgers are migrated in place, terminal ones are not**
(`LPR-R2-I02`, round-2 local plan review; supersedes revision 1's "left
lowercase, not migrated" text, which round 1's `LPR-R1-I03` had only
asked to be *stated* accurately, not reconciled against
`WORKFLOW_V2_3_FOLLOWUPS.md` item 3's own migration requirement #2/#4 —
round 2 did that reconciliation and it comes out the other way). By the
time this checkpoint lands, `workflow-v2-3-followups`'s own
`plan_review_stages` already holds whatever casing this item's own
plan-review rounds wrote, under the pre-CP3 lowercase-writing code
(confirmed: this item's own two-stage plan-review protocol completes
before CP1, let alone CP3, per "Independent inspection" below). Leaving
that live, non-terminal record unmigrated would directly contradict item
3's migration requirement #2 ("existing live state is migrated
deliberately where appropriate") and required follow-up #4 ("Migrate
current live workflow state to the canonical values") — requirement #4's
own companion requirement #4 half about *immutable historical review
evidence* not needing rewriting covers `workflow-v2-3`'s terminal record,
not this item's own live one.

CP3 therefore adds one small, targeted migration function,
`migrate_plan_review_stage_keys(state: dict) -> dict`
(`scripts/workflow_state.py`): for every work item whose `phase` is *not*
in `TERMINAL_PHASES`, if its `plan_review_stages` dict is not `None`,
replace it with the result of `normalize_plan_review_stages` (above,
`GPT-FUP-R6-I02`) — **revised this revision**: reusing the exact same
collision-aware helper the read sites use, not a raw per-key
`_normalize_plan_review_stage_key` pass, so migration and
compatibility-reading agree on what "canonical" means, and on what happens
when a conflicting duplicate is found, by construction. An already-
ambiguous non-terminal work item's `plan_review_stages` (byte-identical
duplicates aside) makes the whole `state_transaction` call raise
`AmbiguousPlanReviewStageKeyError` rather than silently pick a winner and
persist it — the migration is a single atomic transaction, so a raised
exception leaves `WORKFLOW_STATE.json` completely unwritten, not partially
migrated, and the operator resolves the conflict by hand before
re-running it. The function is idempotent (an
already-canonical dict round-trips unchanged) and generic (it does not
hardcode `workflow-v2-3-followups`'s own work-item id), even though this
item is the only live `"2.1"` item today. CP3's implementation runs it
once, via `workflow_state.state_transaction(repo_root,
migrate_plan_review_stage_keys)`, as a one-time normalization step against
the live `WORKFLOW_STATE.json` in the same commit as the code/template
changes — not a permanent runtime call on every state read, and not a
standalone CLI/command surface (no new entry point is added; REQ-11's
"no new lifecycle states/ledgers/quorum" is unaffected, since this changes
key casing only, never the shape or meaning of the ledger). `workflow-v2-3`'s
own terminal `plan_review_stages` block is skipped by the `TERMINAL_PHASES`
guard and stays byte-unchanged.

**Aliasing removal condition, now checkable** (closes migration
requirement #6, "any compatibility aliasing has a clearly documented
removal/deprecation policy"): after this migration runs, the
compatibility-read normalization becomes removable exactly when no live
(non-terminal-phase) work item's `plan_review_stages` dict holds a
legacy-cased key — true immediately after this checkpoint's migration
step, for every work item that exists at that moment, and re-checkable at
any later point by the same read-side normalization + a grep for the two
legacy tokens outside terminal-phase records. Terminal records
(`workflow-v2-3`'s own, and any future one) are permanently excluded from
that condition by design, since they are immutable historical evidence,
not live state — the alias therefore stays supported for as long as any
terminal record exists holding the legacy casing, which is expected to be
indefinitely, and that is the intended, documented shape of the policy
rather than an open-ended unresolved question.

**Conformance-surface obligation** (`LPR-R1-B02`): this checkpoint changes
`review-plan.md`'s and `record-manual-plan-review.md`'s real bytes (the
`Reviewer role:` template casing and the exact-match expectation,
respectively) — both are golden-hash-pinned entries in
`workflow_integration_test.py`'s `_GOLDEN_COMMAND_FILE_SHA256`. Both
entries must be updated to their new post-edit hashes, in the same commit
as the corresponding template edit, with the dictionary's own
rationale-comment convention. (No other exact-literal test asserts either
file's specific casing beyond the golden hash — confirmed by grep; the
`WORKFLOW_V2_PLAN.md`/`manual_external_plan_review`-as-value occurrences
in `workflow_integration_test.py` are unrelated and correctly untouched.)

Regression coverage:

- A fresh `record_local_plan_review`/`record_manual_plan_review` call
  writes the canonical uppercase key.
- A hand-constructed state dict using the **legacy** lowercase
  `plan_review_stages` keys is still read correctly by
  `plan_approval_gate_reachable`/`_validate_plan_review_stages` — the
  compatibility-read proof.
- **A non-terminal work item holding legacy lowercase
  `plan_review_stages` keys still reads and behaves correctly before
  migration runs** (`LPR-R1-I03`): a work item at a non-terminal phase
  (e.g. `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`) whose `plan_review_stages`
  dict uses the legacy lowercase keys, driven through
  `plan_approval_gate_reachable` and/or a further state transition,
  reads and behaves correctly via the compatibility-read helper — proving
  the read side tolerates legacy casing independently of whether
  migration has run yet (relevant to any future `"2.1"` item observed in
  that window, and to a hand-authored fixture generally).
- **`migrate_plan_review_stage_keys` migrates every live non-terminal
  work item's legacy-cased keys to canonical casing, is idempotent, and
  leaves terminal-phase records untouched** (`LPR-R2-I02`): a fixture
  with one non-terminal work item holding legacy lowercase keys and one
  terminal-phase work item holding the same is run through the migration
  function; the non-terminal record's `plan_review_stages` dict comes out
  canonical-cased with `review_content_id` and all nested values
  unchanged, the terminal record is byte-identical to its pre-migration
  form, and running the function a second time changes nothing further.
  The migrated non-terminal record additionally validates cleanly through
  `_validate_work_item`/`validate_state` — proving the migration produces
  a state the rest of the codebase accepts, not just a dict that looks
  right.
- `validate_manual_plan_review_preconditions` accepts a feedback file
  whose `Reviewer role:` line uses either casing, and refuses a third,
  malformed value cleanly (no silent pass-through).
- **Mixed legacy+canonical duplicate, conflicting values, refuses cleanly**
  (`GPT-FUP-R6-I02`): a hand-constructed `plan_review_stages` dict holding
  both a `manual_external_plan_review` and a `MANUAL_EXTERNAL_PLAN_REVIEW`
  key with different record values raises `AmbiguousPlanReviewStageKeyError`
  from `normalize_plan_review_stages`, naming both raw keys — constructed
  both ways (each raw key inserted first) to prove the refusal is
  independent of dict insertion order. The same fixture driven through
  `migrate_plan_review_stage_keys` raises the identical error rather than
  persisting a partial migration.
- **Mixed legacy+canonical duplicate, identical values, collapses cleanly**
  (`GPT-FUP-R6-I02`): the same shape but with byte-identical record values
  under both raw keys collapses to that one shared value with no error,
  both via `normalize_plan_review_stages` directly and via
  `migrate_plan_review_stage_keys` — the "provably identical duplicates are
  tolerated" rule the finding required be specified and tested.
- `workflow-v2-3`'s own terminal `WORKFLOW_STATE.json` entry is
  byte-unchanged after this checkpoint lands (proves historical evidence
  needed no mutation) — this is the same guarantee the migration fixture
  above proves mechanically; this bullet is the real-repository
  confirmation against the actual file.

Housekeeping: `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md`'s item 3
`Status` field updated to record this checkpoint's closure of required
follow-up items 1–6 and 8, and the "tests, fixtures" half of item 3's own
required follow-up #7 (**corrected attribution, `LPR-R3-O02`**, round-3
local plan review: revision 3 also listed #9 — "re-run the full workflow
test suite before freezing/tagging the reusable baseline," REQ-12 — as
closed by this checkpoint; that is CP4's work, not CP3's, and every
checkpoint runs the suite, so CP3's own closure list is corrected to
1–6 and 8 only). The remaining half of #7 is recorded as explicitly
deferred, not closed: it covers specifically the untracked
`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` and
`docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg` (named by
path, not by the category phrase "operator documentation, and the
lifecycle/reference diagram" — **corrected, `LPR-R3-O02`**: this
checkpoint itself updates two *tracked* operator-facing documents,
`MILESTONE_WORKFLOW.md` and `PLAN_REVIEW_WORKFLOW.md`, so the category
phrase would misstate what CP3 defers) — see `LPR-R2-I01`'s disposition
in CP4 below, which covers both item 2's #8 and these same two deferred
paths together.

### CP4 — Final cross-cutting verification and protocol/workflow doc coherence check

(Renamed from "...operator-reference sync", `LPR-R1-I04`: the only
document in this repository actually called an operator reference is
`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`, which this plan
explicitly excludes from its own scope and artifacts declaration —
"operator-reference sync" risked reading as a license to edit it. What
this checkpoint actually does is re-verify `REVIEW_PROTOCOL.md` and
`MILESTONE_WORKFLOW.md`, both edited in CP2, still read coherently
together.)

No new logic. Re-run the complete existing Python workflow test suite,
including both real-repository demo suites (`workflow_state_demo_test.py`,
`workflow_fingerprint_demo_test.py`), confirming zero regressions from
CP1–CP3 combined. Cross-check that
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Local reviewer commands" section,
`docs/ai-workflow/MILESTONE_WORKFLOW.md`'s "Hard gates summary" sentence,
`MILESTONE_WORKFLOW.md`'s `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
"Allowed actions" clause, and `MILESTONE_WORKFLOW.md`'s "Feedback file
locations" section (all four edited in CP2, the third added to the
inventory by `LPR-R4-I01` and the fourth by `LPR-R8-I02`) still read
coherently together and that `/review-functional`'s own unchanged
behavior — including its own untouched `AWAITING_FUNCTIONAL_REVIEW`
"Allowed actions" clause — is stated unambiguously.
Confirm the hard-gate count is still exactly 6. Confirm, by a final grep
sweep, that no `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, new
implementation-review lifecycle state, ledger stage, reviewer-quorum
concept, or controller-lifecycle content was introduced anywhere across
CP1–CP3 — the explicit out-of-scope list this plan's own "Scope note"
names, including the `Reviewer role:` marker CP2 explicitly declined to
add to `/review-implementation`. Confirm
`docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md`'s four items each carry an
accurate, current `Status` reflecting this work item's disposition of
them: item 4 closed in CP2; item 1 closed in CP1; item 2 closed in CP2
except its own required follow-up #8; item 3 closed in CP3 except the
"operator documentation, and the lifecycle/reference diagram" half of its
own required follow-up #7 — **not** "items 1–3 closed here" without
qualification, which would overstate what this milestone actually closes.

**Disposition of item 2 #8 and item 3 #7, decided explicitly**
(`LPR-R2-I01`, round-2 local plan review): both sub-items name the same
two artifacts — `docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`
and `docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg` — and
both are untracked, concurrent, out-of-scope scratch documentation left
over from a previous session (confirmed live: `git status --porcelain`
reports both `??`), excluded from this item's own artifacts declaration
at both stages per CLAUDE.md's "don't touch unrelated working-tree
changes." **Decision: decline both for this milestone.** Bringing them
into scope would mean reclassifying both paths as implementation-stage
protected in a regenerated artifacts declaration and giving CP2/CP3 a
corresponding obligation to actually finish (not merely track) product
content this process work item does not own — content a concurrent
session may still be actively drafting. This is explicitly **not**
silent: `WORKFLOW_V2_3_FOLLOWUPS.md` item 2's `Status` and item 3's
`Status` are both updated (see CP2's and CP3's own "Housekeeping" notes
above) to record that these two specific sub-items remain open against
`WORKFLOW_V2_3_FOLLOWUPS.md`'s own "Follow-up policy after v2.3" /
"Post-v2.3 sequence" — which ties both to "before freezing the reusable
workflow baseline" — so they are carried forward as a named, tracked
prerequisite of that later step rather than lost. This milestone's own
`/accept-milestone` closes `workflow-v2-3-followups` without closing
these two sub-items; the baseline-freeze step itself is the point at
which they must be revisited, either by finishing and committing the two
operator-facing documents first or by re-scoping them out of the freeze
gate explicitly at that time.

## Requirements traceability

| Requirement | Description | Checkpoints |
|---|---|---|
| REQ-1 | Fix `/approve-review plan`'s conditional fifth-member staging bug: replace the two-call staging sequence with one coherent guarded staging operation, preserving the empty-index/dirty-index protection, conditional fifth-member pinned-SHA verification, the durable approval journal, exact staged-set assertions before commit, and the current state-pin/staging order. | CP1 |
| REQ-2 | Add a regression test exercising the real conditional fifth-member path with actual Git staging; verify the no-fifth-member approval path still behaves identically; verify rollback/retry semantics remain correct when staging or commit creation fails. Update `_GOLDEN_COMMAND_FILE_SHA256["approve-review.md"]` to the new post-edit hash, with a rationale comment. | CP1 |
| REQ-3 | Change `/review-implementation` so it writes the authoritative current `REVIEW_FEEDBACK.md` directly, following `/review-plan`'s operator-facing writeback pattern, without importing plan-specific ledger/state-transition semantics. | CP2 |
| REQ-4 | Add pre-write and immediate-pre-write freshness/rejection checks so a review cannot overwrite feedback after its subject becomes stale during the review; define safe replacement semantics for feedback belonging to an older implementation bundle, explicitly including the cross-work-item flat-`feedback_dir` case (decision, **revised twice**: round-6 `GPT-FUP-R6-I01` first resolved this by making `/review-implementation`'s own write target work-item scoped via `ensure_scoped_feedback_dir`; round-7 local plan review (`LPR-R7-B01`) rejected that fix as a silent-shadowing hazard against every operator-facing doc still naming the flat path, and this revision resolves it instead with a refusal guard, `assert_feedback_not_owned_by_other_work_item`, that leaves `resolve_feedback_dir` completely untouched — see REQ-21). Split `REVIEW_PROTOCOL.md`'s "that choice is always the user's, never automatic" sentence by command, and record the explicit decision not to add a `Reviewer role:` provenance field to `/review-implementation`'s output, accepting that a locally-authored round can reach `EXTERNAL_APPROVE` with no on-disk authorship marker (`LPR-R2-B01`). | CP2 |
| REQ-5 | Add regression coverage proving newly written feedback binds successfully through `parse_review_feedback_binding_fields`/`assert_feedback_matches_bundle`; add regression proving stale prior feedback is replaced only by a successful review of the current bundle; add negative tests proving a stale/rejected/wrong-phase implementation review writes nothing. Update `_GOLDEN_COMMAND_FILE_SHA256["review-implementation.md"]`, rewrite `TestReviewImplementationCommandStaticConformance`'s two report-only exact-literal assertions to the new narrower invariant, and update `EXPECTED_ASSERTION_COUNT["...review-implementation.md"]` from 1 to 2 with its rationale comment — verified by running `workflow_state_demo_test.py` within this checkpoint, not deferred to CP4. The plan-conformance doc-assertion test (REQ-6) is added as a further method on `TestReviewImplementationCommandStaticConformance`, not by mirroring `workflow_integration_test.py:1342` (`LPR-R2-O02`). | CP2 |
| REQ-6 | Apply the deferred plan-conformance-read follow-up: add `<bundle_dir>/PLAN.md` and `plan_path` to `/review-implementation`'s read list and a plan-conformance search arm to its verification step, mirroring `/review-plan`'s existing instruction. | CP2 |
| REQ-7 | Leave `/review-functional` completely unchanged; update shared documentation only to state its unchanged behavior unambiguously alongside `/review-implementation`'s new behavior. | CP2, CP4 |
| REQ-8 | Normalize persisted review-role/review-stage identifiers (`local_model_plan_review`, `manual_external_plan_review`) to canonical `SCREAMING_SNAKE_CASE` for all new writes, while readers/parsers remain able to understand legacy lowercase values, immutable historical review evidence is not rewritten, and every live non-terminal work item's already-persisted legacy-cased ledger is migrated to canonical casing in the same checkpoint (`LPR-R2-I02`), refusing cleanly via `AmbiguousPlanReviewStageKeyError` rather than resolving by dict-iteration order if a work item's ledger ever holds two conflicting raw keys for one canonical stage (`GPT-FUP-R6-I02`; see REQ-22). | CP3 |
| REQ-9 | Update reviewer-role templates and exact-token validation (`/review-plan`, `/record-manual-plan-review`), live canonical operator documentation, and test fixtures wherever these tokens appear in active code paths — without touching completed work items' immutable plan documents, dry-run evidence, or untracked concurrent scratch documentation. Extend the read-site inventory to `/review-plan` step 7's round-computation prose and `/record-manual-plan-review`'s exact-match-expectation prose, both of which are model-performed reads outside the Python compatibility-normalization helper (`LPR-R2-O01`.1–.2). | CP3 |
| REQ-10 | Add regression tests proving canonical new values are written, legacy values remain readable where compatibility is required, malformed/ambiguous values still refuse cleanly — including a mixed legacy+canonical duplicate with conflicting values, refused by `normalize_plan_review_stages`'s `AmbiguousPlanReviewStageKeyError`, independent of dict key-iteration order, alongside a byte-identical duplicate collapsing cleanly (`GPT-FUP-R6-I02`; see REQ-22) — historical evidence does not need mutation to remain readable, and a non-terminal work item holding legacy lowercase `plan_review_stages` keys reads correctly via the compatibility-read helper; add a regression proving `migrate_plan_review_stage_keys` migrates every live non-terminal work item's legacy-cased ledger to canonical casing, is idempotent, leaves terminal-phase records byte-unchanged, and produces a state that validates through `_validate_work_item`/`validate_state`; document the compatibility-aliasing removal/deprecation policy in terms of a real, checkable condition ("no live non-terminal work item holds a legacy-cased key," true immediately after this checkpoint's migration) rather than an open-ended alias (`LPR-R2-I02`). Update `_GOLDEN_COMMAND_FILE_SHA256["review-plan.md"]`/`["record-manual-plan-review.md"]` to their new post-edit hashes, with rationale comments. | CP3 |
| REQ-11 | Do not add `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, new implementation-review lifecycle states or ledger stages, reviewer quorum, or controller-specific lifecycle redesign anywhere in this milestone — including as an inert `Reviewer role:` provenance marker on `/review-implementation`'s output (REQ-4's decision). | CP1, CP2, CP3, CP4 |
| REQ-12 | Re-run the full existing Python workflow test suite, including both real-repository demo suites, with zero regressions; keep this a focused maintenance milestone that preserves the proven lifecycle. | CP4 |
| REQ-13 | State the explicit disposition of `WORKFLOW_V2_3_FOLLOWUPS.md` item 2's required follow-up #8 and item 3's required follow-up #7's "operator documentation, and the lifecycle/reference diagram" half: both declined for this milestone (untracked, out-of-scope scratch content, per CLAUDE.md's git restrictions), and both recorded in the source document's own `Status` fields as remaining open against the "before freezing the reusable workflow baseline" step, not silently dropped (`LPR-R2-I01`). | CP2, CP3, CP4 |
| REQ-14 | Add the trailer final-paragraph requirement sentence to `approve-review.md` step 6.4, mirroring `milestone-implement.md`/`bootstrap-workflow-v2.md` verbatim, with a doc-assertion proving it; name the `classify_plan_approval_outcome` → `AMBIGUOUS` → "stop immediately" consequence in "Executing this plan"; decline `accept-milestone.md`'s identical gap for this milestone and record it by name as an out-of-scope, same-defect-class gap (`LPR-R3-B01`). | CP1 |
| REQ-15 | Close the pre-existing `workflow_state_demo_test.py` red baseline at `base_commit`: add `fb134ac4f7cdabb7861d170bb61331bb8d9f5a14` to `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS` with a rationale comment, and add `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md` to `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`'s implementation-stage `excluded_paths`, matching commit `aaa1247`'s precedent — so CP2's and CP4's full-suite runs need no baseline-relative carve-out (`LPR-R3-B01`, `LPR-R3-I01`). | CP1 |
| REQ-16 | Correct "Independent inspection"'s "never dispatched on by name" claim about the `step=` label; name the merged staging window's own step label, `"step-5-stage-and-pin"`, add it to `PLAN_APPROVAL_ORDINARY_STEPS` in the same commit as the command-choreography change, and add a regression test proving `plan_approval_step_class("step-5-stage-and-pin")` returns `"ordinary"` and that the guard is acquired under it; leave the now-unacquired `"step-6.2-stage-ordinary"` **and `"step-5-declaration-pin"`** in place, documented as intentionally inert rather than removed, each with its own call-site count (`LPR-R3-B02`, `LPR-R4-I03`). | CP1 |
| REQ-17 | Add `review-implementation.md`'s own step 6 and step 7 provenance statements to CP2's edit inventory alongside the `REVIEW_PROTOCOL.md` sentence, with the same per-command precision (`/review-functional`'s equivalent language stays true and untouched); correct the "existing step 6, unchanged in shape" phrasing so it does not read as "unchanged in text"; add a doc-assertion proving neither sentence still reads as manual operator installation (`LPR-R3-I02`). | CP2 |
| REQ-18 | Rewrite "Independent inspection" item 3's migration paragraph so it states revision 3's actual disposition — this item's own non-terminal `plan_review_stages` ledger is migrated in place by CP3's one-time `migrate_plan_review_stage_keys` run, `workflow-v2-3`'s terminal record is not — and adjust item (b)'s reader-tolerance justification to match; keep both cross-references to this paragraph (CP3's design, twice) pointed at the timing claim they actually rely on (`LPR-R4-B01`). | CP3 |
| REQ-19 | Add `MILESTONE_WORKFLOW.md`'s `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` "Allowed actions" clause to CP2's doc-sync inventory alongside the "Hard gates summary" sentence, with the same precision (still non-gating, still advances no `phase`, still writes no `WORKFLOW_STATE.json`, but no longer "writes nothing"); state that `/review-functional`'s own equivalent `AWAITING_FUNCTIONAL_REVIEW` clause stays true and untouched; extend CP4's coherence cross-check to cover it (`LPR-R4-I01`). | CP2, CP4 |
| REQ-20 | Add `review-implementation.md`'s frontmatter `description:` line to CP2's edit inventory, narrowed the same way as the two report-only assertions; state explicitly that the frontmatter's `state_writer: false` and `review-subject: bundle` fields are verified-correct-and-unchanged rather than merely unmentioned (`LPR-R4-I02`). | CP2 |
| REQ-21 | Resolve `GPT-FUP-R6-I01` without `resolve_feedback_dir`-side effects (`LPR-R7-B01`, round-7 local plan review, superseding revision 7's `ensure_scoped_feedback_dir` mechanism): immediately before `/review-implementation`'s write, call the new `workflow_fingerprint.assert_feedback_not_owned_by_other_work_item(existing_content, work_item_id=work_item_id)` against whatever content already sits at the unmodified `resolve_feedback_dir(repo_root, work_item_id)` path, and refuse with `FeedbackOwnedByOtherWorkItemError` (`scripts/workflow_fingerprint.py`, beside `MissingFeedbackBindingFieldError`/`FeedbackBundleMismatchError`, `LPR-R8-O01`), naming both work item ids, when that content's own `Work item:` binding field names a different work item. `resolve_feedback_dir` itself, and every other command's resolution against it, are never touched. This guard runs only after the write's own other guards pass, so the four negative "writes nothing" paths still create nothing on disk. `/review-plan`'s own inherited flat-fallback exposure for a work item that has not yet run `/review-implementation` is explicitly not changed. On a refusal from this guard, print the composed report in full exactly as step 7 already does, and state the refusal alongside it — the operator loses only the write, not the completed review; on a refusal from the second `assert_bundle_not_rejected` call, suppress the report entirely, exactly as the existing step 4 call already does — this checkpoint's write does not change that call's withdrawal invariant (`LPR-R9-B01`, round-9 local plan review, narrowing `LPR-R8-I01`, which had generalized report-printing to either guard). Hand-creating a scoped `.ai-review/<work_item_id>/feedback/` directory is explicitly not an endorsed remedy (`LPR-R7-B01`); when the blocking file's own `Work item:` value names a work item A tracked in `docs/ai-workflow/WORKFLOW_STATE.json` and *live* — its `phase` still advancing toward `MILESTONE_COMPLETE` on some scheduled cause, not dormant (`LPR-R12-I01`, round-12 local plan review, narrowing this rule so the dormant branch below reads as a disjoint case rather than a contradiction) — the operator must leave it in place and wait. A's feedback is live for the entire span between the round that wrote it and A's own terminal phase, not just two phases (`LPR-R10-B01`, round-10 local plan review, correcting `LPR-R9-I01`'s two-member consumption-keyed set, which inverted the guard's purpose): it is unconsumed while A's `phase` is `AWAITING_EXTERNAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, or `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, and consumed-but-still-read while A's `phase` is `REVISING_PLAN` (`MILESTONE_WORKFLOW.md`'s own entry condition for that phase is exactly "`REVIEW_FEEDBACK.md` exists"), `APPLYING_REVIEW_FEEDBACK` (entered only by `/apply-implementation-review` step 0's own `enter_applying_review_feedback`, `scripts/workflow_state.py:9087-9107`), or `AWAITING_PLAN_APPROVAL`/`AWAITING_TECHNICAL_APPROVAL` (`approve-review.md` step 1 reads `REVIEW_FEEDBACK.md` for the most recently reviewed round's status/bundle ID there too, and a deleted file forfeits A's `EXTERNAL_APPROVE` basis). `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is not an edge case: it is the exact phase this checkpoint's own new write leaves A in immediately after `/review-implementation A` runs, so the recovery must never call that phase spent. Only once a live A's `phase` independently reaches `MILESTONE_COMPLETE` — the phase at which this plan is willing to guarantee no later command reads the file for A, not merely the only phase at which one happens to be absent (`LPR-R11-O01`, round-11 local plan review) — may the operator delete it by hand and re-run; deletion, not the re-run alone, is what clears the refusal. Two work items both parked at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` against the same flat path genuinely block each other until one reaches `MILESTONE_COMPLETE`, which can be a long wait. When the blocking file's own `Work item:` value names no entry in `WORKFLOW_STATE.json` at all, there is no `phase` to consult; the operator judges it by hand from the file's own `Reviewed bundle ID:`/`Reviewed base commit:` fields and may delete it if it is leftover (`LPR-R10-O01`, round-10 local plan review). When that value instead names an entry that is tracked but dormant — its `phase` not advancing toward `MILESTONE_COMPLETE` on any scheduled cause, `LEGACY_READY` named explicitly since `scripts/workflow_state.py:294-295` documents it as "dormant, not terminal" and `milestone-8` occupies it today — the phase signal is not a wait either; the operator judges the file by hand from its own `Reviewed bundle ID:`/`Reviewed base commit:` fields exactly as the untracked branch, and may delete it if it is leftover (`LPR-R11-I02`, round-11 local plan review). Add a regression proving one work item's pending flat-path feedback survives byte-identical across a different work item's refused `/review-implementation` write, and that the refused run creates no `.ai-review/<work_item_id>/feedback/` directory as a side effect (`LPR-R8-I01`), plus the same-work-item and no-existing-file paths still succeed. | CP2 |
| REQ-22 | Resolve `GPT-FUP-R6-I02`: replace the bare per-key dict-comprehension sketch for reading `plan_review_stages` with a dedicated `normalize_plan_review_stages` helper (`scripts/workflow_state.py`) used at all three read sites and by `migrate_plan_review_stage_keys`; when two raw keys normalize to the same canonical stage, collapse silently only if their values compare equal, otherwise raise `AmbiguousPlanReviewStageKeyError` (new exception, `scripts/workflow_state.py`, beside its ten sibling plan-review-stage exceptions — corrected from eight, `LPR-R12-O03`, round-12 local plan review — corrected from `scripts/workflow_fingerprint.py`, `LPR-R7-I01`) naming both raw keys — never resolving by dict key-iteration order. Add regressions for both the conflicting-refusal and identical-collapse cases, exercised at both the read-helper and migration-function levels, with the refusal case proven independent of insertion order. | CP3 |
| REQ-23 | Add `MILESTONE_WORKFLOW.md`'s "Feedback file locations" section to CP2's doc-sync inventory, correcting "Both are read, never written, by Claude" to state `REVIEW_FEEDBACK.md` is written by `/review-plan` (pre-existing) and, after this checkpoint, by `/review-implementation` too, while `FUNCTIONAL_REVIEW.md` stays read-only; extend CP4's coherence cross-check from three sites to four (`LPR-R8-I02`, round-8 local plan review). | CP2, CP4 |

## Checkpoint registry (generated; do not hand-edit — regenerated at each
## plan revision from `scripts/workflow_state.py`'s `generate_registry`)

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Fix /approve-review plan fifth-member staging bug | - | 3 | 1 |
| CP2 | /review-implementation writes canonical REVIEW_FEEDBACK.md + plan-conformance read | CP1 | 3 | 2 |
| CP3 | Normalize review-role/review-stage identifiers to SCREAMING_SNAKE_CASE | CP2 | 3 | 2 |
| CP4 | Final cross-cutting verification and protocol/workflow doc coherence check | CP3 | 1 | 1 |

CP1's prerequisite is only `base_commit` itself — no other checkpoint needs
to land first. CP2 depends on CP1 landing first (sequential per the user's
own numbered scope, and to keep `approve-review.md` in one coherent state
between checkpoints — CP2 does not technically require CP1's fix, but
sequencing them keeps the diff each checkpoint reviews small and coherent,
matching `workflow-v2-3`'s own linear CP1→CP2 chain). CP3 depends on CP2
for the same reason (CP2 leaves `review-implementation.md` and
`workflow_state.py` in a settled state before CP3 touches
`workflow_state.py` again). CP4 depends on CP3 completing, since it
verifies all three fixes together.

Each checkpoint: commit its own changes, **then** — not before — run the
full existing Python suite (`workflow_fingerprint_test.py`,
`workflow_fingerprint_generalization_test.py`, `workflow_state_test.py`,
`workflow_state_completion_obligations_test.py`, `workflow_integration_test.py`,
`workflow_test_harness_test.py`, from `scripts/`) to confirm zero
regressions before moving to the next checkpoint. CP4 additionally re-runs
the two real-repository demo suites
(`workflow_fingerprint_demo_test.py`, `workflow_state_demo_test.py`).

## Self-review (`SELF_REVIEWING_PLAN`)

- **Missing requirements?** All three mandatory items plus the
  opportunistic fourth from `WORKFLOW_V2_3_FOLLOWUPS.md`'s own "Follow-up
  policy after v2.3" section are covered (REQ-1 through REQ-10). The
  policy's explicit exclusion list (new lifecycle states, ledger/quorum,
  controller redesign, `/review-functional` changes) is captured as its
  own cross-cutting requirement (REQ-11) rather than left implicit, so a
  reviewer can check it directly against each checkpoint rather than
  trusting the "Scope note" prose alone.
- **Migration risk?** None of the three fixes touch Room, backups, or any
  product schema — this is process tooling only, no Android build/install
  step. CP3's identifier normalization is the closest thing to a
  migration. **Corrected across two rounds** (`LPR-R1-I03` then
  `LPR-R2-I02`, round-1 and round-2 local plan review): "Independent
  inspection" above establishes that no live non-terminal work item holds
  the legacy values *as of `base_commit`*, but this item's own two-stage
  plan-review ledger writes those exact legacy keys during its own plan
  approval, before CP1 let alone CP3 — so by the time CP3 lands, this
  item's own in-flight, non-terminal ledger does hold them. Revision 3
  reconciled that fact against `WORKFLOW_V2_3_FOLLOWUPS.md` item 3's own
  migration requirement #2/#4 and now migrates rather than leaves it
  (`LPR-R4-O01`, round-4 local plan review: the reversal came from
  round-2's `LPR-R2-I02` finding but was applied in revision 3, not
  revision 2 — round and revision numbers are off by one throughout this
  plan's history): CP3
  runs `migrate_plan_review_stage_keys` once against the live
  `WORKFLOW_STATE.json`, converting every live non-terminal work item's
  legacy-cased `plan_review_stages` keys to canonical casing (this item's
  own being the only one that exists today) while leaving terminal-phase
  records — `workflow-v2-3`'s own — byte-unchanged. Reader-tolerance
  (proven against a non-terminal fixture, not only the terminal
  `workflow-v2-3` record) remains in place regardless, covering any
  legacy-cased state observed in the window before migration runs, any
  hand-authored fixture, and `workflow-v2-3`'s own permanently-unmigrated
  terminal record.
- **Usability gaps?** CP2 is the requirement with the most direct operator
  impact (removing the manual copy/paste step `WORKFLOW_V2_3_FOLLOWUPS.md`
  item 2 itself flags as the main friction point); its design reuses
  `/review-plan`'s already-proven writeback shape rather than inventing a
  new one, minimizing the chance of a second copy/paste-shaped defect.
  This item's own first `/approve-review plan` run is itself a usability
  defect the plan now documents rather than leaves as a surprise — see
  "Executing this plan" above (`LPR-R1-B01`).
- **Unnecessary complexity?** CP1's fix is deliberately the smallest
  change that closes the gap — no new `workflow_state.py` function, since
  `stage_plan_approval_commit_paths` already accepts the combined path
  set. CP3 adds one normalization-and-collision-detection helper
  (`normalize_plan_review_stages`, built on the existing key-level
  `_normalize_plan_review_stage_key` primitive), one new exception class
  (`AmbiguousPlanReviewStageKeyError`), and two constants — not an enum
  refactor or a broader identifier-taxonomy rework. The plan's own
  "Design" section explicitly declines to invent a staleness-detection
  subsystem for CP2 where reusing `/review-plan`'s existing guard-then-
  overwrite pattern already satisfies the requirement. It no longer
  declines to close `LPR-R1-I02`'s cross-work-item exposure — **revised
  twice**: revision 7 (`GPT-FUP-R6-I01`) first resolved it for
  `/review-implementation`'s own new automatic write with a sibling
  function, `ensure_scoped_feedback_dir`, that scoped the write target
  ahead of time; round-7 local plan review (`LPR-R7-B01`) found that fix
  reached further than it claimed to — it would have been the first
  mechanism in this repository ever scoping a work item's feedback
  directory, silently outrunning every operator-facing doc that still
  names the flat path. This revision resolves it instead with one small
  guard function, `assert_feedback_not_owned_by_other_work_item`, that
  refuses on a genuine cross-work-item collision rather than relocating
  the write — smaller still than revision 7's fix, since it touches no
  resolver and requires no operator-doc changes, and closes exactly
  `GPT-FUP-R6-I01`'s own stated guarantee rather than a broader one.
- **Missing tests?** Each checkpoint's design section above enumerates its
  own regression coverage, including at least one negative case per
  checkpoint (CP1: dirty-index-still-refuses; CP2: three independent
  write-nothing paths; CP3: malformed-value-still-refuses) — no checkpoint
  ships with positive-only coverage. Revision 2 added each checkpoint's own
  **conformance-surface** obligation (golden hashes CP1/CP2/CP3 each move,
  CP2's two exact-literal report-only assertions, CP2's
  `EXPECTED_ASSERTION_COUNT` update verified inside CP2 itself rather than
  deferred to CP4) and a non-terminal legacy-key transition case
  (`LPR-R1-B02`, `LPR-R1-I01`, `LPR-R1-I03`). Revision 3 adds the
  `migrate_plan_review_stage_keys` fixture (idempotent, migrates every
  live non-terminal record, leaves terminal ones byte-unchanged, validates
  through `_validate_work_item`) and CP2's plan-conformance doc-assertion
  landing on the correct existing precedent class rather than a
  mis-cited line (`LPR-R2-I02`, `LPR-R2-O02`) — none of which revision 2
  named. Revision 4 adds CP1's new-step-label acquisition case, CP1's
  `approve-review.md` final-paragraph doc-assertion, and CP2's step
  6/step 7 provenance-text doc-assertion (`LPR-R3-B01`, `LPR-R3-B02`,
  `LPR-R3-I02`) — none of which revision 3 named. Revision 5 extends CP2's
  own doc-assertion method one further `assertNotIn` to cover the
  frontmatter `description:` line (`LPR-R4-I02`); CP1's
  `"step-5-declaration-pin"` disposition needs no new test, since its own
  seventeen existing call sites already fail closed if the entry were
  removed, the same reasoning already given for the grandfathered set
  (`LPR-R4-I03`); and `MILESTONE_WORKFLOW.md`'s "Allowed actions" sentence
  gets no new test either, by explicit decision, matching the "no test
  pins this file's prose today, and adding the first one is out of this
  checkpoint's scope" disposition stated where the inventory entry itself
  is added (`LPR-R4-I01`). Revision 6 adds no new tests: `LPR-R5-I01`,
  `LPR-R5-I02`, and `LPR-R5-O01`–`O04` are all plan-document and
  declaration-rationale corrections, none testable and none acquiring a
  test — including CP1's disposition of the `PLAN_APPROVAL_ORDINARY_STEPS`
  comment's declared-but-unacquired enumeration, which stays a hand-
  maintained comment nothing in the suite pins today, the same reasoning
  already given for the two step labels themselves. Revision 7 added four
  new tests responding to round-6 manual external plan review's two
  Important findings: CP2's two-work-item feedback-isolation case and
  `ensure_scoped_feedback_dir`'s own idempotency (`GPT-FUP-R6-I01`), and
  CP3's mixed legacy+canonical duplicate-key conflicting-refusal and
  identical-collapse cases, each exercised at both the
  `normalize_plan_review_stages` and `migrate_plan_review_stage_keys`
  levels (`GPT-FUP-R6-I02`). `GPT-FUP-R6-O01`'s dedicated-helper request is
  satisfied by construction (the same `normalize_plan_review_stages`
  function is now used at all three read sites and by migration) rather
  than needing a test of its own, and `GPT-FUP-R6-O02` is a plan-document
  evidence-organization instruction for CP1, not a test. **Superseded,
  this revision** (`LPR-R7-B01`, round-7 local plan review): revision 7's
  two-work-item feedback-isolation case and `ensure_scoped_feedback_dir`
  idempotency case are replaced, not kept alongside, by the refusal-based
  case described in CP2's design above — `ensure_scoped_feedback_dir` no
  longer exists in this plan for a test to exercise. Revision 8 adds no
  further new test beyond that replacement: `LPR-R7-I01` (exception
  placement) and `LPR-R7-O01` (blast-radius statement) are both
  plan-document/rationale corrections, not testable surfaces of their
  own — the fail-closed behavior `LPR-R7-O01` documents is already
  exercised by CP3's existing `AmbiguousPlanReviewStageKeyError`
  regressions. Revision 9 adds two, both responding to round-8 local plan
  review's `LPR-R8-I01`, the only round-8 finding with a testable surface:
  the two-work-item refusal fixture is extended to also assert no
  `.ai-review/<work_item_id>/feedback/` directory exists after the refused
  run, and a further `TestReviewImplementationCommandStaticConformance`
  method confirms `review-implementation.md`'s own prose states the
  refusal-path report-printing and recovery behavior. `LPR-R8-I02`
  (`MILESTONE_WORKFLOW.md`'s "Feedback file locations" disposition) and
  `LPR-R8-O01` (exception module naming) are both plan-document
  corrections, not testable surfaces of their own, the same disposition
  the equivalent findings received in earlier rounds. Revision 10 adds no
  further new test, responding to round-9 local plan review's
  `LPR-R9-B01`/`LPR-R9-I01`: both are corrections to prose that revision 9
  itself had already added a test for — the refusal-path doc-assertion
  method now pins the narrowed, per-guard statement instead of the
  "either pre-write guard" wording it previously asserted (same method,
  reworded assertion, `LPR-R9-B01`), and the recovery text's
  consumption-keyed correction (`LPR-R9-I01`) is operator documentation
  with no test surface of its own — the behavioral half (refuse, leave A's
  file byte-identical, create no scoped directory) is already fully
  covered by REQ-21's existing refusal regression. `LPR-R9-O01`
  (the artifacts declaration's `MILESTONE_WORKFLOW.md` rationale) is a
  plan-document/declaration correction, not a testable surface, the same
  disposition the equivalent finding received in round 5 (`LPR-R5-O03`).
  Revision 11 adds no further new test, responding to round-10 local plan
  review's `LPR-R10-B01`/`LPR-R10-O01`: both are corrections to the
  recovery prose revision 9 itself had already added a test for — the
  refusal-path doc-assertion method now pins the terminal-phase-keyed
  statement instead of the two-phase consumption wording it previously
  asserted (same method, reworded assertion, `LPR-R10-B01`), and the
  corrected recovery text (including the untracked-`Work item:` clause,
  `LPR-R10-O01`) is operator documentation with no test surface of its
  own — the behavioral half (refuse, leave A's file byte-identical, create
  no scoped directory) remains fully covered by REQ-21's existing refusal
  regression, unaffected by which recovery prose sits above it. Revision 12
  adds no further new test either, responding to round-11 local plan
  review's `LPR-R11-I01`/`LPR-R11-I02`/`LPR-R11-O01`: all three are
  corrections to the same recovery prose that revision 9 itself had already
  added a test for — the refusal-path doc-assertion method now pins the
  label-free terminal-phase statement instead of a mismatched numeric phase
  count (same method, reworded assertion, `LPR-R11-I01`), and the new
  dormant-tracked-item clause and the softened "sole phase" justification
  (`LPR-R11-I02`/`LPR-R11-O01`) are operator documentation with no test
  surface of their own — the behavioral half remains fully covered by
  REQ-21's existing refusal regression, unaffected by which recovery prose
  sits above it. Revision 13 adds no further new test either, responding
  to round-12 local plan review's `LPR-R12-I01`/`LPR-R12-O01`/`LPR-R12-O02`/
  `LPR-R12-O03`: `LPR-R12-I01` is a correction to the same recovery prose
  that revision 9 itself had already added a test for — the refusal-path
  doc-assertion method now pins "that only a live A reaching
  `MILESTONE_COMPLETE` clears the refusal for deletion" instead of the
  unqualified statement it previously asserted (same method, reworded
  assertion), and the behavioral half remains fully covered by REQ-21's
  existing refusal regression, unaffected by the qualifier; `LPR-R12-O01`,
  `-O02`, and `-O03` are inventory, disposition, and vocabulary
  corrections with no testable surface of their own — `-O01`'s behavioral
  half (the two-value acceptance itself, and a genuinely malformed third
  value still refusing cleanly) is already covered by CP3's existing
  `validate_manual_plan_review_preconditions` regression.
- **Carried-over findings from `workflow-v2-3`'s own final review?**
  (`LPR-R1-O03`) Two Optional findings closed that item with this
  maintenance pass in mind. **`GPT-IR4-O02`** (pair the deferred
  plan-conformance read with the planned `/review-implementation`
  writeback) — this plan implements it (REQ-6, folded into CP2); this
  sentence records that disposition explicitly, which revision 1 did not.
  **`GPT-IR4-O01`** (consider deriving or mechanically checking simple
  roster/classification counts instead of duplicating them in prose) —
  **declined for this milestone**: mechanizing count derivation across
  `_GOLDEN_COMMAND_FILE_SHA256`, `EXPECTED_ASSERTION_COUNT`, and similar
  hand-maintained tables is a tooling investment broader than this
  focused maintenance pass's own scope, and REQ-11 already forbids
  reaching for broader redesign here. Round 1's `LPR-R1-B02`/`LPR-R1-I01`
  and now round 2's `LPR-R2-B01`/`LPR-R2-I01`/`LPR-R2-I02` findings are
  the fifth and sixth consecutive rounds to hit variants of exactly the
  failure mode `GPT-IR4-O01` warned about — a hand-maintained requirement
  or conformance surface silently drifting from the thing it is supposed
  to track — which strengthens rather than weakens the case for picking
  it up in a future, dedicated item, but that remains deliberately not
  this one.
- **Artifacts-declaration template fit** (required check per
  `docs/ai-workflow/MILESTONE_WORKFLOW.md`'s `SELF_REVIEWING_PLAN` entry):
  `generate_artifacts_declarations`'s default template is boilerplate
  copied from `workflow-v2-1-core`'s own original declarations (confirmed:
  its default `.claude/commands/` plan-stage rationale text literally
  mentioned "the bootstrap command", which does not exist in this item's
  scope). This plan corrected that stale text and hand-widened both
  stages' `excluded_paths`/`excluded_prefixes`/`protected_paths`/
  `protected_prefixes` to this item's own real footprint *before*
  publishing this revision — including product-repo docs, other work
  items' immutable plan documents, and the untracked
  `WORKFLOW_V2_1_OPERATOR_REFERENCE.md`/`diagrams/` scratch content — up
  front, rather than rediscovering gaps revision-by-revision the way
  `workflow-v2-3`'s own plan document's revision history shows it doing.
  `docs/ai-workflow/registry/workflow-v2-3-followups-artifacts.json` is
  the result. **Correction, `LPR-R2-O03`** (round-2 local plan review):
  the implementation stage's `excluded_prefixes` rationale for
  `docs/ai-workflow/registry/` claimed "this item's own registry.json and
  artifacts.json are carved out above by exact path, checked first" —
  only `artifacts.json` is in that stage's `protected_paths`;
  `registry.json` is (correctly) implementation-stage `excluded`, not
  protected, since the registry is plan-stage-governed and not an
  implementation deliverable. The behavior was already correct; only the
  rationale sentence was inaccurate, in the one file this declaration
  itself protects specifically so its contents are reviewable at face
  value — corrected in this revision to name only `artifacts.json`.
- **Open decisions**: `docs/TECHNICAL_DECISIONS.md`'s "Open decisions"
  table checked in full (all ten rows read directly, not assumed from
  memory). All ten are product/domain decisions (Gradle module split, Room
  schema, progression formulas, navigation, backup location/retention,
  notification permission behavior, cross-day workouts, substitution
  effects on progression history, manual correction of completed workouts,
  UI design system) — none implicated by this process-tooling change, the
  same disposition `workflow-v2-3`'s own plan recorded for the same table.

## Deferred to Workflow v3 (not planned here)

Restated from `WORKFLOW_V2_3_FOLLOWUPS.md`'s own "Follow-up policy after
v2.3" section, unchanged by this plan:

- new local-vs-external implementation-review lifecycle states, in
  particular `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`;
- an implementation-review ledger/quorum redesign, analogous to the plan
  stage's `plan_review_stages`;
- controller-specific orchestration states;
- richer multi-reviewer approval semantics;
- any change to `/review-functional`;
- broader workflow lifecycle redesign generally.

These remain deferred until the Workflow Manager / Bootstrapper and
Controller exist and have a concrete orchestration need for them, per
`WORKFLOW_V2_3_FOLLOWUPS.md`'s own "Post-v2.3 sequence" diagram — this
plan's own CP1–CP4 are exactly the three-plus-one items that sequence
places immediately before "freeze/tag the reusable workflow baseline" and
"begin Workflow Manager / Bootstrapper", nothing past that point.
