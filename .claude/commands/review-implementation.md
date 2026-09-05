---
description: Independently review the current implementation bundle for the active (or named) work item and write the current advisory REVIEW_FEEDBACK.md. Writes <feedback_dir>/REVIEW_FEEDBACK.md -- never writes WORKFLOW_STATE.json, never approves, never applies findings, never advances phase.
argument-hint: "[work-item-id]"
state_writer: false
review-subject: bundle
---

Enters no new state — this is an optional, non-gating action available
while the resolved item's `phase` is exactly
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
(`docs/ai-workflow/MILESTONE_WORKFLOW.md`). Implements a model-independent
**review role**, not a specific model: nothing in this contract, in the
report it produces, or in any check it performs names a model — running it
from any capable Claude model produces the same behavior.

**Writes `<feedback_dir>/REVIEW_FEEDBACK.md`; nothing else.** This command
writes the current `<feedback_dir>/REVIEW_FEEDBACK.md` (step 7, once every
guard there passes) but never writes
`docs/ai-workflow/WORKFLOW_STATE.json`, never edits source/test/plan/
registry/mapping/bundle content, never approves a stage, and never
advances `phase`. `/apply-implementation-review` remains the sole
authoritative path for validating and applying real implementation-review
feedback, and `/approve-review implementation` remains the sole, user-only
approval gate — both entirely unchanged and unaffected by this command's
existence.

`<bundle_dir>`/`<feedback_dir>` below resolve per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir`).

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`). Refuse cleanly, naming the
   problem, if neither resolves to an existing `work_items` entry — this
   command needs a live `phase` value to guard against; an ordinary `"1"`
   item that never got a state entry has no `phase` to check, so use
   `/prepare-review` for an ad-hoc, untracked review of that kind of item
   instead.
2. **Phase guard**: if the resolved item's `phase` is not exactly
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, refuse cleanly, naming the
   actual phase. This guard is version-independent by design: reachable for
   a `governing_workflow_version` of `"1"` or `"2.1"` alike, since
   `docs/ai-workflow/MILESTONE_WORKFLOW.md` defines this state identically
   for both. That version-independence claim covers only this phase guard —
   whether a usable bundle actually exists once the guard passes is a
   separate, generation-time precondition, orthogonal to
   `governing_workflow_version` and not implied by it (see step 3/4's
   manifest precondition below).
3. **Read**: `<bundle_dir>/PLAN.md` and the item's own `plan_path` (the
   authoritative plan doc, for step 5's plan-conformance arm below),
   alongside `<bundle_dir>/REVIEW_REQUEST.md`, `IMPLEMENTATION_SUMMARY.md`,
   `TEST_RESULTS.md`, `CHANGED_FILES.txt`, `COMMITS.txt`, `DIFF.patch`,
   `files/`, `MANIFEST.md`, the required-context file list, and any prior
   `<feedback_dir>/REVIEW_FEEDBACK.md` for continuity across rounds. An
   implementation-stage bundle generated without the plan stage's mandatory
   work-item-id argument lands in the flat `.ai-review/current/` path while
   the resolver still prefers a scoped `.ai-review/<work_item_id>/current/`
   directory if one exists — an inherited hazard, not new here (`/approve-
   review` has the same one); step 4's `assert_local_generation_matches`
   normally surfaces the resulting staleness, but if it doesn't, report the
   mismatch by naming both candidate paths (`.ai-review/<work_item_id>/current/`
   and the flat `.ai-review/current/`) rather than only the resolved one.
   **Manifest precondition**: an implementation/post-fix bundle generated
   without that same optional work-item-id argument
   (`./scripts/prepare-ai-review.sh <base-sha> implementation`, no third
   argument — a documented, supported invocation, e.g.
   `.claude/commands/milestone-implement.md`'s own `[work_item_id]`) carries
   **no implementation-stage `MANIFEST.md` at all**:
   `scripts/prepare-ai-review.sh` writes one only in the `-n "$WORK_ITEM_ID"`
   branch. Step 4's `compute_bundle_id` call fails closed on this with
   `MissingRequiredBundleFileError` — a clean, expected refusal for this
   specific case, not a corrupt-bundle symptom; report it as such and tell
   the user to regenerate scoped
   (`./scripts/prepare-ai-review.sh <base-sha> implementation
   <work_item_id>`).
4. **Recompute fresh, before reporting anything**: an absent or unreadable
   bundle is a clean refusal here, named plainly rather than left to
   surface as a raw traceback — both candidate `<bundle_dir>` paths
   (`.ai-review/<work_item_id>/current/` and the flat `.ai-review/current/`)
   named in the refusal. Do not rely on the later
   `assert_local_generation_matches` call to catch this case: its default
   `require_metadata=False` mode reads `MANIFEST.md`'s
   `worktree_root:`/`generation_head:` lines only if present and performs
   **no comparison at all**, without raising, when `MANIFEST.md` is simply
   absent (`read_manifest_generation_metadata` returns `{}` for a missing
   file). The actual failure is the `bundle_id`/`review_content_id`
   recompute immediately below, in this same step: `compute_bundle_id`
   raises `MissingRequiredBundleFileError` naming the missing required
   file(s) the moment it is called against an absent or incomplete bundle
   directory — that is the exception this step's refusal is built around.
   The current `bundle_id` and the implementation-stage `review_content_id`
   (`scripts/workflow_fingerprint.py`), computed via
   `compute_review_content_id_implementation_stage_at_commit(repo_root,
   base=work_item["base_commit"], commit="HEAD", ...)` (or equivalently
   `workflow_state.approval_review_content_id(..., stage="implementation",
   base_commit=work_item["base_commit"], head="HEAD")`, which wraps it) —
   commit-source, anchored at exactly the `base`/`head` pair
   `workflow_state.approval_review_content_id`'s own implementation-stage
   branch uses — **never** the worktree-source
   `compute_review_content_id_implementation_stage` (no `head`/`commit`
   parameter at all, scoped instead to whatever is currently dirty), which
   would not reproduce the value `MANIFEST.md` records or `/approve-review
   implementation` itself checks (the generator's sole writer,
   `write_manifest_with_verified_identifiers_implementation_stage`, is
   commit-source too) — with the resolved item's own four classification
   mappings loaded through
   `workflow_fingerprint.load_implementation_stage_classification(repo_root,
   artifacts_path=workflow_fingerprint.artifacts_path_for_work_item(work_item_id))`
   — never that function's own default `artifacts_path`, which resolves to
   `workflow-v2-1-core`'s artifacts file and would silently compute a
   different work item's classification. Report, rather than silently
   proceeding past, any mismatch against what `MANIFEST.md`/`REVIEW_REQUEST.md`
   claim. **Stale-plan-stage-manifest variant**: if `MANIFEST.md` is present
   and otherwise looks healthy but the recomputed implementation-stage
   `review_content_id` still disagrees with what it records, name this
   specific cause explicitly — an unscoped implementation/post-fix bundle
   written into a directory that already holds a plan-stage `MANIFEST.md`
   silently reuses that plan-stage manifest, so the mismatch is comparing
   today's implementation-stage recompute against a stale plan-stage
   identity, not a real content discrepancy — report it as that, not as an
   unexplained digest mismatch. This command runs inside a real, current
   worktree, so also call
   `workflow_fingerprint.assert_local_generation_matches(repo_root,
   <bundle_dir>/MANIFEST.md)` and stop, naming both the recorded and current
   worktree_root/HEAD, on a `WorktreeOrHeadMismatchError` — the same
   repository-local staleness discipline `/approve-review`/`/review-plan`
   already apply. **Dominant cause at this phase**: at
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `generation_head` sits at
   `/milestone-implement`'s own generation-record commit, which lands
   *before* generation by contract, so a healthy bundle satisfies
   `generation_head == HEAD` exactly; the realistic way this check fails
   here is a concurrent excluded-only commit landing after generation — the
   case `/recover-implementation-provenance` exists to repair, and the same
   cause `/approve-review`'s own step 1 names for this phase. Report a
   `WorktreeOrHeadMismatchError` naming that cause and pointing at
   `/recover-implementation-provenance`, not only the raw recorded/current
   values. This check and the digest recompute above catch different
   failure modes, exactly as `/approve-review`'s own step 2 states: content/
   digest binding (`bundle_id`/`review_content_id`) detects protected-content
   drift, while `assert_local_generation_matches` detects repository-local
   staleness (a different worktree, or a commit landed past the recorded
   `generation_head`). For the excluded-only concurrent-commit case
   specifically, the digest recompute stays clean, not mismatched:
   excluded paths sit outside the protected-content projection and live
   `HEAD` is never hashed into `review_content_id`
   (`compute_review_content_id_implementation_stage_at_commit`'s own
   documented concurrent-excluded-write durability property) — so
   `bundle_id`/`review_content_id` never mismatch for this case at all.
   The `WorktreeOrHeadMismatchError` is the only signal it produces; do
   not expect, or wait for, an accompanying digest mismatch to corroborate
   it. **`REJECTED`-bundle refusal, first of two** (`WFR-67`; step 7 below
   re-calls this immediately before the write): call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here.
5. **Independently verify** every claim `IMPLEMENTATION_SUMMARY.md`/
   `TEST_RESULTS.md` makes against the actual repository state — rerun the
   tests `TEST_RESULTS.md` names yourself rather than trusting its reported
   outcome; read `DIFF.patch`/`files/` directly rather than only the
   summary prose. Search independently for correctness bugs, architecture/
   layering violations (route to `docs/adr/0003-layered-modular-architecture.md`
   if relevant), migration/backup-format risk (route to
   `docs/adr/0002-offline-first-local-database-source-of-truth.md` if
   relevant), missing tests, and usability concerns — exactly as thoroughly
   as `/apply-implementation-review`'s own step 2 validation requirement.
   **Also independently verify the implementation against what the
   approved plan (`PLAN.md`/`plan_path`, read in step 3) actually
   specified** — mirroring `/review-plan` step 6's "independently verify
   every finding... against the actual repository state" instruction —
   not only against the bundle's own self-reported disposition.
6. **Compose the report** in exactly `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
   `REVIEW_FEEDBACK.md` structure (`Status: APPROVE | REVISE | BLOCK`,
   Blocking/Important/Optional findings, Missing tests, Architecture and
   maintainability concerns, Migration and data-integrity concerns,
   Usability concerns, Required acceptance criteria), plus the three
   binding fields (`Reviewed bundle ID:`, `Reviewed base commit:`,
   `Work item:`) stated with this invocation's own freshly recomputed
   values — since satisfying
   `workflow_fingerprint.parse_review_feedback_binding_fields`/
   `assert_feedback_matches_bundle` is a hard precondition of this
   command's own write in step 7 below, not a convenience for a
   hypothetical hand-copy. Also state
   `Reviewed review content ID:` with step 4's freshly recomputed
   implementation-stage `review_content_id` — not one of the three parsed
   binding fields, so no parser or approval requirement changes; purely so
   the printed advisory opinion is easy to correlate against the exact
   reviewed implementation content.
7. **`REJECTED`-bundle refusal, second of two, then the ownership guard,
   immediately before the write** (`WFR-67`: this is a genuine
   classification change, not just a count bump — this command moves from
   the "once" report-only consumer group to the "twice" mutation-guarded
   group `apply-plan-review`, `approve-review`, `record-manual-plan-review`,
   and `review-plan` already occupy, since a real write now follows the
   guard):
   - Re-call `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
     work_item_id)` — a withdrawal landing between step 4 and here must
     still be caught. **On a `BundleRejectedError` here, suppress the
     composed report entirely** (produce no report output, only the
     refusal), exactly as the existing step 4 call already does — this
     checkpoint's write does not change that withdrawal invariant.
   - Resolve `<feedback_dir>` via the existing, unmodified
     `resolve_feedback_dir(repo_root, work_item_id)`, read whatever
     `REVIEW_FEEDBACK.md` already sits there (`None` if nothing does), and
     call `workflow_fingerprint.assert_feedback_not_owned_by_other_work_item(
     existing_content, work_item_id=work_item_id)` against it — the
     ownership guard runs immediately before the write, against this same
     unmodified `resolve_feedback_dir(repo_root, work_item_id)` path.
     **On a `FeedbackOwnedByOtherWorkItemError` here, still print the
     composed report in full**, exactly as before this checkpoint, and
     state the refusal alongside it, naming both work item ids — the
     operator loses only the write, not the completed review.
   - **Recovery from an ownership refusal**: hand-creating a scoped
     `.ai-review/<work_item_id>/feedback/` directory is **not** an endorsed
     remedy — it would reproduce, by hand, the same silent-shadowing hazard
     this guard exists to prevent. The blocking file's own `Work item:`
     value names a work item A. If A is tracked in
     `docs/ai-workflow/WORKFLOW_STATE.json` and live (its `phase` still
     advancing toward `MILESTONE_COMPLETE` on some scheduled cause, not
     dormant — `LEGACY_READY` is dormant, not terminal), the operator must
     leave the file in place and wait: re-running this command before A
     reaches a terminal phase changes nothing, since nothing in this
     repository deletes or relocates `REVIEW_FEEDBACK.md` as a side effect
     of A's own review cycle. A's feedback is live — unconsumed and
     waiting, or consumed but still read by a later command — for the
     entire span between the round that wrote it and A's own terminal
     phase, `MILESTONE_COMPLETE`; `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
     (the exact phase this command's own write leaves A in immediately
     after it runs) is not an exception to that rule, so the recovery must
     never treat that phase as spent. Only once a live A's `phase`
     independently reaches `MILESTONE_COMPLETE` may the operator delete the
     blocking file by hand and re-run — deletion, not the re-run alone, is
     what clears the refusal. If the blocking file's `Work item:` value
     names no entry in `WORKFLOW_STATE.json` at all, or names a
     tracked-but-dormant entry (e.g. `LEGACY_READY`), there is no
     phase-based wait to honor; the operator judges the file by hand from
     its own `Reviewed bundle ID:`/`Reviewed base commit:` fields and may
     delete it if it is leftover.
   - **Self-check the composed text, immediately before the write**
     (`workflow-v2-3-followups` continued scope, closes the gap step 6's
     own "hard precondition" wording named but this step did not actually
     enforce): call `workflow_fingerprint.parse_review_feedback_binding_fields`
     on the composed report text itself, then
     `workflow_fingerprint.assert_feedback_matches_bundle(<the parsed
     fields>, bundle_id=<step 4's freshly recomputed bundle_id>,
     base_commit=work_item["base_commit"], work_item_id=work_item_id)` —
     the exact same values step 6 was instructed to state. A failure here
     means the composed text itself drifted from what step 4 actually
     recomputed (a transcription slip in the very fields `/approve-review
     implementation` binds against): fix the composed text and re-run this
     check before writing — never write text that fails its own
     self-check.
   - Once both guards pass and the self-check above succeeds, write
     `<feedback_dir>/REVIEW_FEEDBACK.md` unconditionally, overwriting
     whatever same-work-item feedback (if any) currently sits there. This
     write is now the authoritative round the moment it lands — no
     separate operator installation step. Still never write
     `docs/ai-workflow/WORKFLOW_STATE.json`, never approve, never advance
     `phase`, never auto-continue to any other command.
8. **Report and stop.** On a successful write, state plainly that
   `<feedback_dir>/REVIEW_FEEDBACK.md` was written and is now the
   authoritative round for `/apply-implementation-review`/`/approve-review
   implementation` to act on — an independent, advisory opinion from
   whichever model ran this command, not a human reviewer's own pass unless
   the operator obtained one separately. `docs/ai-workflow/WORKFLOW_STATE.json`
   and every other repository file remain untouched. Never approve, never
   apply findings, never transition `phase`, never auto-continue to any
   other command.

Do not implement product or test code in this command. Do not edit the
plan/registry/mapping/artifacts files or any other command file.
