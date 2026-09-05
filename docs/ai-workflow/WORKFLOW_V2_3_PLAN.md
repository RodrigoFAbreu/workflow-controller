# Workflow v2.3 — Reviewer-Command Ergonomics (Revision 13)

Execution/reference plan for work item `workflow-v2-3`. Governed by
`governing_workflow_version: "2.1"` (the two-stage local-then-manual-external
plan-review protocol applies to this item's own plan-stage approval — see
`docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`).

## Revision 3 — `local_model_plan_review` round 2 disposition

Round 2 (`local_model_plan_review`, reviewing revision 2's bundle
`e47a9c48b030f3299d978dab3a98713b80b03aaf2fca2001dc3e115fb26b2950`) returned
`Status: REVISE` with 2 Blocking, 3 Important, 2 Optional findings and 1
missing-tests item, all independently re-verified against the live
repository before being applied — none was taken on faith (every cited
line/quote was re-read directly; every cited test failure was re-run and
reproduced exactly as reported). Every finding is **accepted**; none is
rejected. In finding order:

- **B1 (accepted)** — verified directly: `classify_path_implementation_stage`
  (`scripts/workflow_fingerprint.py:1568-1593`) has no default branch (fails
  closed via `UnclassifiedPathError`), and the generated
  `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s
  `implementation_stage` carries only a self-reference `protected_paths`
  entry — everything else empty. Re-ran
  `load_implementation_stage_classification(repo_root,
  artifacts_path=artifacts_path_for_work_item("workflow-v2-3"))` against
  this plan's own stated CP1/CP2 footprint directly: all eleven non-self
  paths the round reported are confirmed `UNCLASSIFIED`. Both "the
  inherited/generated declaration is correct" sentences in "Independent
  inspection"/"Self-review" (revision 2) were wrong to extend that
  confirmation to the implementation stage — corrected below to scope it to
  the plan stage only. **Decision**: CP1 gains a new first step (before the
  command file is created, since the declaration is its own
  `implementation_stage.protected_paths` entry and must exist before any
  bundle generation reads it) that hand-widens
  `workflow-v2-3-artifacts.json`'s `implementation_stage`:
  - `protected_prefixes`: `.claude/commands/` (the two new workflow-command
    files this item creates) and `scripts/` (this item's own tooling edits:
    `workflow_state.py`, `workflow_fingerprint.py`,
    `workflow_integration_test.py`, `workflow_state_demo_test.py`) — same
    content category `workflow-v2-1-core-artifacts.json`'s own
    implementation stage already uses these exact two prefixes for.
  - `protected_paths` (beyond the existing self-reference): add
    `docs/ai-workflow/MILESTONE_WORKFLOW.md` and
    `docs/ai-workflow/REVIEW_PROTOCOL.md` — this item's own edits to both
    (the "Allowed actions"/"Hard gates summary" bullets; the new "Local
    reviewer commands" section) describe the two new commands' actual
    behavior, design-adjacent implementation content, not mechanical
    application of already-approved text — mirroring
    `workflow-v2-1-core-artifacts.json`'s own identical reasoning and
    identical entries for these same two paths.
  - `excluded_paths`: add `CLAUDE.md` (mechanical command-list depointer
    implementing already-approved design, mirroring
    `workflow-v2-1-core-artifacts.json`'s identical entry/reasoning) and
    `docs/ai-workflow/WORKFLOW_STATE.json` (runtime-mutable per-work-item
    state, same reasoning as every other artifacts declaration).
  - `excluded_prefixes`: add `docs/ai-workflow/registry/` and
    `docs/ai-workflow/requirements/` (plan-stage-governed registry/mapping
    files, not implementation deliverables — this item's own plan doc,
    registry, and mapping fall here; the declaration file's own path stays
    carved out above by exact path, checked first), matching
    `workflow-v2-1-core-artifacts.json`'s identical prefixes/reasoning.

  This is the first artifacts-declaration widening this item performs at
  CP1 (see B2 immediately below for the second, on a different file, fixing
  a different problem) — both must land before CP1's own verification step
  runs the real-repository suites.
- **B2 (accepted)** — re-ran `workflow_fingerprint_demo_test.py`/
  `workflow_state_demo_test.py` directly at this bundle's own working tree
  (base `27f051eb`) and reproduced exactly the reported 4 errors + 1 failure
  (`fingerprint_demo_test`: `failures=1, errors=3`) and 1 error + 1 failure
  (`state_demo_test`, the latter being I2 below, not this finding) — all
  five `UnclassifiedPathError: docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`, all
  five confirmed this item's own regression (committed-only diff since
  `162154d3` classifies exhaustively; adding the working tree adds exactly
  this one unclassified path). Root cause, confirmed by reading each
  failing test directly: `workflow_fingerprint.py`'s frozen
  `PLAN_STAGE_PROTECTED`/`PLAN_STAGE_EXCLUDED_*` constants and
  `workflow-v2-1-core-artifacts.json`'s **default** implementation-stage
  classification (used when `load_implementation_stage_classification` is
  called with no `artifacts_path` override) both predate this item's
  existence and were never designed to recognize a second, concurrent work
  item's own plan document. **Decision, mixed remedy, both halves assigned
  to CP1** (measured baseline and both options' costs below, per the
  round's required acceptance criterion):
  1. **Four of the five** — `test_demonstration_against_real_repo`,
     `test_142_generalized_resolver_reproduces_the_migrated_digest_not_a_hardcoded_literal`,
     `test_real_diff_since_base_commit_classifies_exhaustively`,
     `test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas`
     (all in `workflow_fingerprint_demo_test.py`) — are re-anchored from
     `base..worktree` scope to `base..HEAD` (committed-only) scope, since
     each one's own stated purpose is validating that `workflow-v2-1-core`'s
     own **committed** history classifies exhaustively, not policing
     whatever a different, concurrently-active work item happens to leave
     uncommitted in the same tree. Concretely: swap
     `compute_review_content_id_plan_stage`/
     `compute_review_content_id_plan_stage_for_work_item` for their
     already-existing commit-source counterparts
     `compute_review_content_id_plan_stage_at_commit`/
     `compute_review_content_id_plan_stage_at_commit_for_work_item`
     (`scripts/workflow_fingerprint.py:1421-1458`, `1480-1489`) with
     `commit="HEAD"`; swap
     `compute_review_content_id_implementation_stage` for
     `compute_review_content_id_implementation_stage_at_commit`
     (`:1717-...`); and change
     `test_real_diff_since_base_commit_classifies_exhaustively`'s own scan
     from `wf._changed_tracked_paths(repo_root, BASE_COMMIT) |
     wf._untracked_paths(repo_root)` to
     `wf._changed_tracked_paths_between(repo_root, BASE_COMMIT, "HEAD")`
     (dropping the untracked half for this one test, matching the other
     three's re-anchoring). Verified this touches **no** frozen
     `PLAN_STAGE_*` constant and **no** artifacts JSON file, so
     `workflow-v2-1-core`'s recorded `plan_approval`/`review_content_id` are
     completely unaffected. This matters concretely, not just in principle:
     the alternative "plan-stage half" remedy (widening
     `PLAN_STAGE_EXCLUDED_PATHS`, required in lockstep with
     `workflow-v2-1-core-artifacts.json`'s `plan_stage` by
     `test_141_migrated_artifacts_plan_stage_equals_frozen_python_constants`'s
     equality assertion) would change `workflow-v2-1-core`'s own plan-stage
     `review_content_id`, and `workflow_state_demo_test.py::test_real_plan_approval_is_current`
     — confirmed **currently passing**, re-run directly — recomputes that
     exact value against the recorded `plan_approval.approved_review_content_id`
     (`43e92efb...`); changing the frozen constants would flip this
     currently-green test red. Re-anchoring avoids that regression entirely
     rather than trading one failure for another.
  2. **The fifth**,
     `workflow_state_demo_test.py::test_real_implementation_stage_classification_has_no_unclassified_dirty_path`,
     cannot be re-anchored this way: its own docstring and code
     (`ws.any_protected_path_dirty` over `load_implementation_stage_classification(repo_root)`,
     the bare default) deliberately test *whatever is dirty in the working
     tree right now* — that is the entire point of a dirty-state safety
     gate, not a historical-diff check. For this one: widen
     `workflow-v2-1-core-artifacts.json`'s `implementation_stage.excluded_paths`
     to add `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`, mirroring that same
     file's own existing, identically-reasoned
     `docs/ai-workflow/WORKFLOW_V2_PLAN.md` entry ("plan-stage design
     content, already governed by the plan approval — not an implementation
     deliverable"). **Cost, stated rather than assumed**: this changes
     `workflow-v2-1-core-artifacts.json`'s blob, which no longer matches the
     blob pinned in `workflow-v2-1-core`'s own
     `technical_approval.review_content_manifest` (recorded `82af58e9...`,
     confirmed equal to the live blob today) — but, verified by direct grep,
     **no** real-repository test currently re-checks `workflow-v2-1-core`'s
     `technical_approval` currency the way `test_real_plan_approval_is_current`
     does for `plan_approval` (there is no
     `test_real_technical_approval_is_current` or equivalent), and the item
     is `MILESTONE_COMPLETE` with no further Workflow v2.1 gate that
     re-evaluates it. Accepted as a disclosed, one-time, no-currently-verified-impact
     classification widening — not a silent edit, and not the higher-cost
     plan-stage remedy option 1 above specifically avoids.
- **I1 (accepted)** — re-read `docs/ai-workflow/WORKFLOW_V2_PLAN.md:30306-30370`
  directly: the three semantic disjuncts (A)/(B)/(C) are confirmed exactly
  as quoted, `/review-functional` is confirmed to satisfy none of them
  literally (it touches no bundle), and the conformance arm's own dual
  check — "against the three semantic disjuncts **and** against the
  presence/absence of the shared assertion in that same file" (`:30358-30361`,
  confirmed verbatim) — means a declaration must be justified against the
  disjuncts, not against "the assertion is present" alone. Revision 2's own
  phrase "`/review-functional` satisfies the `assert_bundle_not_rejected`-call
  criterion B2 establishes" is circular for exactly this reason: it cites
  the presence of the call as justifying the declaration that licenses the
  call. **Decision**: the "Design" section's `/review-functional`
  disposition is corrected below to state the declaration as what it
  actually is — a deliberate **widening** of what `review-subject: bundle`
  answers, from "can a marked bundle become this command's subject" ((B)/(C))
  to "does this command consult the work-item-scoped `REJECTED` marker" — not
  a claim that `/review-functional` satisfies (B) or (C) as originally
  defined. Reconciled explicitly with `/prepare-functional-review`'s `none`
  (confirmed at `WORKFLOW_V2_PLAN.md:30437-30441`, quoted verbatim below):
  after this widening, `/prepare-functional-review` is the one command
  reachable from `AWAITING_FUNCTIONAL_REVIEW` that does *not* consult the
  marker. This is not a new inconsistency: `/prepare-functional-review` is
  purely a **generator** act (it writes fresh checklist content into
  `docs/ACTIVE_MILESTONE.md`; it has no report/presentation act of its own,
  the user's own manual pass is what "presents" the checklist) — the same
  "generator, scoped by act" exemption `WORKFLOW_V2_PLAN.md:30428-30436`
  already grants `scripts/prepare-ai-review.sh`/`/prepare-review`'s
  generation path, which must never be blocked by a marker it might itself
  be about to clear or reconfirm. `/review-functional`, by contrast, is
  purely a **report** act (it writes nothing) reading an *existing* round's
  evidence — the consuming half of that same act/generator split, not a
  peer of the checklist-writing half. Recorded here as the stated
  reconciliation the round asked for, and flagged as a known input to the
  deferred `WF8c` `review-subject:` re-derivation (out of this item's small,
  ergonomics-only scope to resolve further).
- **I2 (accepted)** — confirmed `27f051eba897d77c742ead8b160ed519c0671ee4`
  (this item's own `base_commit`, `workflow-v2-1-core`'s accept-milestone
  commit) fails
  `workflow_state_demo_test.py::test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set`
  by re-running it directly, and confirmed by reading the commit message
  (`git log -1 --format=%B 27f051e`) that its `Workflow-Work-Item:` line
  really does sit before a blank line and further `Co-Authored-By:` prose,
  so it is not the message's own final paragraph — exactly the defect class
  `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`
  (`scripts/workflow_state_demo_test.py:154-195`) already exists for.
  Pre-existing at this item's own base commit, not this item's regression
  (unlike B2's five). **Decision**: add
  `27f051eba897d77c742ead8b160ed519c0671ee4` to
  `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`, explicitly named
  per that set's own established discipline (individually grandfathered,
  never silently excluded) — bundled into CP1's same new first step as B1/B2
  above, since all three land before CP1's own verification step runs
  `workflow_state_demo_test.py`.
- **I3 (accepted)** — re-read `TEST_RESULTS.md`'s revision-1 section
  directly: it states the generated artifacts declaration's classification
  "was read in full and confirmed to fit this item's own plan footprint"
  and that "no automated test suite applies." Per B1/B2 above, the first
  claim is false for the implementation-stage half and the plan-stage half
  leaves this item's own plan document unclassified under the real-repository
  suites' sets; the second claim is true only of *unit* suites; the two
  `_demo_test.py` files scan the live working tree and do apply. **Decision**:
  `<bundle_dir>/TEST_RESULTS.md` is corrected in place (not this plan
  document, which the bundle copies verbatim from `docs/ai-workflow/`) to
  narrow both claims to the plan stage and to record this round's real
  command output (the exact failures B1/B2/I2 above reproduced), rather than
  the unqualified/incorrect statements.
- **O1 (accepted)** — verified both token-level hazards directly.
  `TestAssertLocalGenerationMatchesCallSiteConformance._CALL_RE =
  re.compile(r"assert_local_generation_matches\(")`
  (`scripts/workflow_integration_test.py:582`) glob-scans literal text, not
  semantic call sites, so `review-functional.md` must never contain that
  exact substring (name-with-open-paren) anywhere, including prose
  contrasting it with `/review-implementation` — the current design text
  ("this command never calls `assert_local_generation_matches` (no
  `MANIFEST.md`...)") already avoids it by not placing an open paren
  immediately after the name, confirmed by direct inspection; the "Design"
  section below now says so explicitly rather than leaving it accidental.
  `_parse_review_subject_declarations`
  (`scripts/workflow_state.py:6799-6803`) collects every whole-line
  `review-subject:` match in a file and requires them identical
  (`ReviewSubjectDeclarationError` on any mismatch) — confirmed by reading
  the function directly — so the new "Local reviewer commands"
  `REVIEW_PROTOCOL.md` section's contrast between `/review-functional`
  (`bundle`) and `/prepare-functional-review` (`none`) must describe the
  difference in prose, never by quoting a literal `review-subject: none`
  line inside a fenced block. Both constraints are now stated explicitly in
  "Shared wiring" below.
- **O2 (accepted)** — confirmed `resolve_rejected_marker_path`
  (`scripts/workflow_fingerprint.py:1885-1891`) resolves to the flat,
  work-item-agnostic `.ai-review/REJECTED` whenever no scoped
  `.ai-review/<work_item_id>/current/` directory exists yet — read directly.
  Revision 2 added a diagnosability sentence for exactly this hazard to
  `/review-implementation` step 3 but not to `/review-functional` step 6,
  even though the functional stage is the more exposed of the two (a work
  item may never have had a scoped bundle directory created by the time it
  reaches `AWAITING_FUNCTIONAL_REVIEW`). Inherited behavior, not a new
  defect (`/apply-functional-review` resolves identically, unchanged).
  **Decision**: one sentence added to `/review-functional` step 6 below,
  naming both candidate paths the same way `/review-implementation` step 3
  already does.
- **Missing tests (accepted)** — confirmed the plan's proposed
  `review-functional.md`-contains-`assert_bundle_not_rejected`-exactly-once
  static assertion exactly duplicates
  `EXPECTED_ASSERTION_COUNT`'s new `review-functional.md: 1` entry
  (`scripts/workflow_state_demo_test.py:1156-1195`,
  `test_every_non_exempt_file_calls_the_shared_assertion_the_expected_number_of_times`
  already counts that exact token in that exact file) — read directly.
  **Decision**: drop the duplicate from the new static-conformance class;
  keep O1's two previously-uncovered constraints instead if a second static
  assertion is wanted (no `assert_local_generation_matches(` substring;
  exactly one distinct `review-subject:` value). Also adds real coverage
  for B1's own defect class, generalized rather than one-off: a new
  `workflow_state_demo_test.py` real-repository test that runs
  `any_protected_path_dirty`/`load_implementation_stage_classification`
  against `artifacts_path_for_work_item(active_work_item_id)` (the live
  active item, not the hardcoded `workflow-v2-1-core` default
  `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`
  already covers) — so a future work item's own under-widened
  implementation-stage declaration fails this suite immediately, the same
  way B1 should have been caught the moment the declaration was generated
  rather than at external review. Bundled into CP1's new first step.

All seven required acceptance criteria from round 2's feedback are
satisfied by the above; see the "Independent inspection", "Design", "Shared
wiring", and "Checkpoint registry" sections below for the corrected text
itself.

## Revision 4 — `local_model_plan_review` round 3 disposition

Round 3 (`local_model_plan_review`, reviewing revision 3's bundle
`314c9e8b7fded27fb6403bf5ac2e03d98edaa7b5ace4301ca9cd10b3f70bbed2`) returned
`Status: REVISE` with 2 Blocking, 2 Important, 2 Optional findings and 2
missing-tests items. The round explicitly confirmed round 2's eight
dispositions all verified cleanly and re-derived every citation from the
live repository rather than reading the disposition table on faith; this
round's two Blocking findings are new defects in *revision 3's own
remedies*, not re-raised round-2 findings. Every finding is **accepted**;
none is rejected. Each was independently re-verified against the live
repository before being applied (function signatures and call graphs read
directly at the cited line numbers, `resolve_plan_stage_approval_commit_paths`'s
base-four-member set confirmed at `scripts/workflow_fingerprint.py:1033-1034`,
`classify_path`/`classify_path_implementation_stage`'s fail-closed
no-default-branch behavior confirmed by direct reading, and
`docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s current
`implementation_stage` re-read in full to confirm it is still exactly the
one-entry self-reference round 2/3 described). In finding order:

- **B1 (accepted, remedy revised)** — verified the round's core claim
  directly: `resolve_plan_stage_approval_commit_paths`'s base four commit
  members are `metadata.plan_path`/`registry_path`/`mapping_path` plus
  `state_path` (`scripts/workflow_fingerprint.py:1000-1004`, `:1034`), so
  `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` (this item's own `plan_path`) is
  committed by this item's own `/approve-review plan` commit, unconditionally,
  before `IMPLEMENTING`/CP1 begins (`D-Approval-Commits`). Also verified
  directly that `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` sits directly under
  `docs/ai-workflow/`, matching none of `PLAN_STAGE_PROTECTED`
  (`scripts/workflow_fingerprint.py:1068-1081`, lists only
  `WORKFLOW_V2_PLAN.md`), `PLAN_STAGE_EXCLUDED_PATHS` (`:1104-1163`, no
  `WORKFLOW_V2_3_PLAN.md` entry), or any `PLAN_STAGE_EXCLUDED_PREFIXES`
  entry (`:1180-1239`; the closest, `docs/ai-workflow/registry/`/
  `docs/ai-workflow/requirements/`, do not match a file directly under
  `docs/ai-workflow/`) — so `classify_path` (`:1251-1263`, no default
  branch, raises `UnclassifiedPathError`) fails closed on it the moment it
  is committed, regardless of which side of the worktree/commit distinction
  a consumer reads from. Also verified directly that
  `workflow_state_demo_test.py::test_real_plan_approval_is_current`
  (`:567-573`) calls `ws.approval_is_current(repo_root, work_item,
  stage="plan", base_commit=work_item["base_commit"])` with no `head=`
  argument, so it defaults to live `"HEAD"`
  (`scripts/workflow_state.py:1545-1546`), which
  `approval_review_content_id(stage="plan", ...)` (`:1528-1532`) passes
  straight into
  `compute_review_content_id_plan_stage_at_commit_for_work_item(repo_root,
  work_item_id, head, base=base_commit)` — so this currently-passing,
  `workflow-v2-1-core`-scoped real-repository test recomputes over
  `162154d3..HEAD` on every run, forever, against whatever `HEAD` happens to
  be at run time. Both of round 3's own two remedy options were re-checked
  against this: leaving `PLAN_STAGE_EXCLUDED_PATHS` unwidened means the
  digest recompute raises `UnclassifiedPathError` the instant
  `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` is committed (`base..HEAD` then
  contains it); widening `PLAN_STAGE_EXCLUDED_PATHS`/
  `workflow-v2-1-core-artifacts.json`'s `plan_stage` in lockstep (required
  by `test_141_migrated_artifacts_plan_stage_equals_frozen_python_constants`'s
  exact-equality assertion) changes what is hashed into the plan-stage
  projection (`"excluded_paths": sorted(excluded_paths)`,
  `scripts/workflow_fingerprint.py:1454`) and therefore changes the digest
  `test_real_plan_approval_is_current` compares against the *unchanged*
  recorded `plan_approval.approved_review_content_id` (`43e92efb...`) — a
  guaranteed mismatch, independent of which commit is passed as `head`,
  since the recorded value was computed under the narrower, pre-widening
  set. Neither of round 3's own two remedy options, nor any widening-based
  variant of them, can keep this test green without also changing what it
  is allowed to assert.

  **Revised remedy, chosen over the round's own three candidates as "a
  better one" (explicitly invited by the round's required acceptance
  criterion 1)**: none of the five real-repository tests this defect class
  touches needs `HEAD` (a moving target) at all — every one of them exists
  to validate a fact about `workflow-v2-1-core`'s own history, and that
  history stopped moving the instant `workflow-v2-1-core` reached
  `MILESTONE_COMPLETE`. Introduce one new named constant in each of
  `scripts/workflow_fingerprint_demo_test.py` and
  `scripts/workflow_state_demo_test.py` —
  `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT =
  "27f051eba897d77c742ead8b160ed519c0671ee4"` (this item's own `base_commit`,
  and independently confirmed to be `workflow-v2-1-core`'s own
  `/accept-milestone` commit by `git log -1 --format=%B`, matching this
  document's own front matter and `docs/ai-workflow/WORKFLOW_STATE.json`) —
  and pass it as the fixed `commit`/`head` argument everywhere these five
  tests currently read live `HEAD` or the live worktree:
  1. `test_demonstration_against_real_repo`: swap
     `compute_review_content_id_plan_stage` (worktree-source) for
     `compute_review_content_id_plan_stage_at_commit`
     (`scripts/workflow_fingerprint.py:1421-1458`), called with
     `commit=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` (not `"HEAD"`), `protected`/
     `excluded_paths`/`excluded_prefixes` unchanged (`wf.PLAN_STAGE_PROTECTED`/
     `wf.PLAN_STAGE_EXCLUDED_PATHS`/`wf.PLAN_STAGE_EXCLUDED_PREFIXES`, still
     frozen, untouched by this remedy).
  2. `test_142_generalized_resolver_reproduces_the_migrated_digest_not_a_hardcoded_literal`:
     same swap, `compute_review_content_id_plan_stage_at_commit_for_work_item(repo_root,
     "workflow-v2-1-core", WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, base=BASE_COMMIT)`
     against the frozen-defaults comparison computed the same way.
  3. `test_real_diff_since_base_commit_classifies_exhaustively`: swap its
     scan from `wf._changed_tracked_paths(repo_root, BASE_COMMIT) |
     wf._untracked_paths(repo_root)` (dirty + untracked) to
     `wf._changed_tracked_paths_between(repo_root, BASE_COMMIT,
     WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)` (committed-only, fixed range).
  4. `test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas`:
     swap `compute_review_content_id_implementation_stage` (worktree-source,
     no head parameter at all) for
     `compute_review_content_id_implementation_stage_at_commit(repo_root,
     BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, ...)`, classification
     mappings unchanged (still `load_implementation_stage_classification(repo_root)`'s
     default, i.e. `workflow-v2-1-core-artifacts.json`).
  5. `workflow_state_demo_test.py::test_real_plan_approval_is_current`: pass
     `head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` explicitly to
     `ws.approval_is_current(...)` instead of relying on the implicit
     `"HEAD"` default.

  Because `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` equals live `HEAD` at the
  moment this revision is written, this is a zero-behavior-change edit
  today (independently confirmed: at that exact commit,
  `docs/ai-workflow/WORKFLOW_STATE.json` contains no `workflow-v2-3` entry
  at all yet, so `resolve_plan_stage_metadata(repo_root, "workflow-v2-1-core",
  at_commit=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)` reads a state file with
  zero exposure to this item's existence, by construction, not by
  incidental timing). It remains correct permanently, for every later
  commit this item's own checkpoints add and for every future concurrent
  work item after it — unlike `commit="HEAD"`, which round 3 chose and
  which this same round measured breaking again the instant this item's own
  plan-approval commit lands. No frozen `PLAN_STAGE_*` constant changes, no
  byte of `workflow-v2-1-core-artifacts.json`'s `plan_stage` changes, so
  `test_141_migrated_artifacts_plan_stage_equals_frozen_python_constants`
  is untouched and stays green throughout, and `workflow-v2-1-core`'s
  recorded `plan_approval.approved_review_content_id` (`43e92efb...`) stays
  byte-exact forever — no re-approval, no digest change, ever, for this
  stage. This is the disposition `test_real_plan_approval_is_current`
  required acceptance criterion 1 asks this document to name explicitly:
  **kept, its recorded digest never invalidated, by pinning its own `head`
  argument rather than by widening any classification set.**
- **B2 (accepted, remedy narrowed — most of it subsumed by B1's revised
  remedy above)** — re-confirmed `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s
  `implementation_stage` is still exactly the one-entry self-reference round
  2/3 described (read in full above this section), and re-ran the exact
  classification the round's table shows: `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`
  and `docs/ACTIVE_MILESTONE.md` both remain `*** UNCLASSIFIED ***` under
  the widening B1's "Revision 3" decision specifies (`docs/ai-workflow/registry/`/
  `docs/ai-workflow/requirements/` prefixes do not match either path;
  neither is a member of the four exact-path additions). Independently
  confirmed `docs/ACTIVE_MILESTONE.md` is written by `/milestone-implement`
  step 1d at *every* checkpoint (`.claude/commands/milestone-implement.md:41`),
  for any work item, process or product alike — so it enters
  `base_commit..HEAD` by the end of CP1 regardless of item type, exactly as
  the round found, and was missing from this document's own stated
  footprint entirely (not merely misclassified). **Decision**: add both as
  exact `implementation_stage.excluded_paths` entries in
  `docs/ai-workflow/registry/workflow-v2-3-artifacts.json` itself (this
  item's *own* declaration, distinct from `workflow-v2-1-core-artifacts.json`
  touched by B1's remedy above), mirroring
  `workflow-v2-1-core-artifacts.json`'s own precedent entries verbatim in
  reasoning:
  - `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`: "plan-stage design content,
    already governed by the plan approval -- not an implementation
    deliverable" (mirrors that file's own `WORKFLOW_V2_PLAN.md` entry).
  - `docs/ACTIVE_MILESTONE.md`: "product-milestone narrative, out of scope
    for this process work item -- written by `/milestone-implement` at
    every checkpoint for any active work item, process or product alike,
    so it must never stale this item's own `technical_approval`" (mirrors
    that file's own `docs/ACTIVE_MILESTONE.md` entry, generalized to state
    the "written by this item's own checkpoints" reason directly rather
    than only the "concurrent item" reason, since here it is *this* item's
    own checkpoint commits that write it, not only a concurrent one's).

  The `excluded_prefixes` rationale sentence in "Revision 3" above ("this
  item's own plan doc, registry, and mapping fall here") is corrected: the
  plan doc does not fall under either prefix (it sits directly under
  `docs/ai-workflow/`, matching neither `docs/ai-workflow/registry/` nor
  `docs/ai-workflow/requirements/`) — it is carved out by the exact-path
  entry above instead, alongside `docs/ACTIVE_MILESTONE.md`; only the
  registry and mapping files are actually covered by the prefixes as
  written.

  Round 3's own remedy for the fifth (dirty-state) test —
  `workflow_state_demo_test.py::test_real_implementation_stage_classification_has_no_unclassified_dirty_path`
  — is **kept**, with its already-disclosed, already-accepted cost
  unchanged (widen `workflow-v2-1-core-artifacts.json`'s
  `implementation_stage.excluded_paths` to add
  `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`; this stales
  `workflow-v2-1-core`'s `technical_approval.review_content_manifest` blob
  pin, with no currently-verified impact, per "Revision 3" B2 above). This
  is the one real-repository test in the whole set that cannot be re-anchored
  to a fixed commit by design — confirmed directly, its own code
  (`ws.any_protected_path_dirty` over `_dirty_paths`,
  `scripts/workflow_state.py:5708-5719`, `git diff --name-only HEAD` plus
  untracked) deliberately tests the live working tree's uncommitted state,
  which has no historical commit to pin to. B1's fixed-anchor remedy makes
  the *other* four originally-failing tests independent of this widening
  entirely (none of them reads `workflow-v2-1-core-artifacts.json`'s
  `implementation_stage` at a moving `HEAD` any more), so this widening's
  scope narrows to exactly the one test it is actually required for.
  Also verified directly: by the time CP1's own verification step runs
  (after CP1's own commit, per I2's revised disposition below), this file
  is never dirty at that moment regardless of this widening — it was
  already committed at the plan-approval commit, which precedes
  `IMPLEMENTING`/CP1 entirely by construction (`D-Approval-Commits`) — so
  this widening is not, strictly, required for CP1's own gate to pass. It
  is kept anyway because the declaration is genuinely incomplete for this
  path (dirty or committed) and leaving it unresolved means the full suite
  reads red for the entire window between plan-approval and CP1's own
  commit, an avoidable false alarm for anyone running it in that window;
  the same already-disclosed cost applies either way, so there is no
  reason not to.
- **I1 (accepted)** — confirmed directly:
  `write_manifest_with_verified_identifiers_implementation_stage`
  (`scripts/workflow_fingerprint.py:3277-3300`) — the sole writer of an
  implementation-stage `MANIFEST.md`, per its own docstring — computes
  `review_content_id` via `compute_review_content_id_implementation_stage_at_commit(repo_root,
  base_full, head_full, ...)`, commit-source, anchored at `head` (the
  final reviewed implementation HEAD). `/approve-review`'s own recompute
  (`workflow_state.approval_review_content_id(stage="implementation", ...)`,
  `:1533-1541`) calls the identical commit-source function with
  `base=base_commit`, `head="HEAD"`. `compute_review_content_id_implementation_stage`
  (no `_at_commit` suffix, `:1667-...`) takes no `head`/`commit` parameter
  at all — it is worktree-source, scoped to whatever changed since `base`
  in the *current working tree*, a categorically different shape from what
  `MANIFEST.md`/`/approve-review` actually compute. The plan's own step 4
  (below, "Design") named this worktree-source function, matching the
  round's finding exactly. **Decision**: step 4 is rewritten to name
  `compute_review_content_id_implementation_stage_at_commit` (or
  equivalently `workflow_state.approval_review_content_id(...,
  stage="implementation")`, which wraps it), with explicit anchors
  `base=work_item["base_commit"]`, `head="HEAD"` — matching
  `/approve-review`'s own usage exactly, so the two commands' recomputes
  are always comparing the same number.
- **I2 (accepted)** — confirmed directly:
  `discover_review_subject_declarations(repo_root, commit)`
  (`scripts/workflow_state.py:6806-6827`) iterates `REVIEW_SUBJECT_ROSTER`
  — a live Python `frozenset` read from the imported module, i.e. the
  *current working-tree* code — but resolves each entry's content *at
  `commit`*, raising `ReviewSubjectDeclarationError` if a roster path does
  not exist there. `test_all_thirteen_command_files_declare_the_expected_value`
  passes live `HEAD`. So the moment CP1's edit to `REVIEW_SUBJECT_ROSTER`
  (adding `.claude/commands/review-implementation.md`) lands in the working
  tree — necessarily before that same new command file's own creation is
  committed — this test fails closed for the entire window until CP1's own
  commit lands, exactly as the round describes; the same shape recurs at
  CP2 for `review-functional.md`. **Decision**: both CP1's and CP2's own
  verification prose (below, "Checkpoint registry") now state explicitly
  that the `_demo_test.py` suites are run *after* that checkpoint's own
  commit, and state why (the roster is read live from the working tree,
  the declarations are read at `HEAD`, so the two only agree once the
  roster edit and the new command file are both committed together).
- **O1 (accepted)** — re-read `docs/ai-workflow/WORKFLOW_V2_PLAN.md:30428-30436`
  directly: the "generator, scoped by act" exemption's stated reason is
  specific — "the marker's writer and its only sanctioned clearer; blocking
  it would leave a marked work item recoverable only by a hand edit" — and
  `/prepare-functional-review` holds neither role (it writes checklist
  content, never the `REJECTED` marker), so this exemption's stated reason
  genuinely does not transfer to it, confirming the round's finding. The
  acceptance criterion the round credits ("or record it as a known input to
  the deferred re-derivation") was already met; only the "This is not a new
  inconsistency" / borrowed-exemption sentence overreached. **Decision**:
  the "Design" section's `/review-functional` disposition (above) drops the
  borrowed-exemption analogy and states plainly that
  `/prepare-functional-review`'s own exemption rests on the different,
  subject-based ground the same source paragraph already states three lines
  later (`:30437-30441`, "takes `FUNCTIONAL_REVIEW.md` as its subject ... so
  no refused bundle can reach it at all") — the same ground
  `/accept-milestone` is given one sentence after that.
- **O2 (accepted)** — confirmed directly, re-running the classification
  table B2 above reproduces: under B1's `implementation_stage` widening,
  `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json` resolves
  `excluded` via the new `docs/ai-workflow/registry/` prefix (checked after
  `protected_paths`/`protected_prefixes` in `classify_path_implementation_stage`'s
  fixed lookup order, `scripts/workflow_fingerprint.py:1568-1593`). B2's
  remedy 2 (above) has CP1 edit exactly that file
  (`workflow-v2-1-core-artifacts.json`'s own `implementation_stage.excluded_paths`,
  to add `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`) — so, unless corrected,
  this item's own edit to another item's declaration never enters this
  item's own implementation-stage `review_content_manifest`, and this
  item's own implementation reviewer's approval never binds to it, even
  though the same file protects itself in `workflow-v2-1-core`'s own set
  for precisely the "widen an exclusion and re-bless the resulting digest
  with no gate ever having seen the change" reason its own self-reference
  entry states (confirmed by reading that entry directly). **Decision**:
  add `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json` as an
  exact `implementation_stage.protected_paths` entry (checked before the
  `docs/ai-workflow/registry/` prefix, by construction) in
  `docs/ai-workflow/registry/workflow-v2-3-artifacts.json` itself, the same
  shape that file already uses for its own self-reference, with rationale:
  "edited by this item's own CP1 (widening its `implementation_stage.excluded_paths`
  per B1/B2) — the same 'an editable-under-no-approval declarations file
  would let someone widen an exclusion and re-bless the resulting digest
  with no gate ever having seen the classification change' reasoning this
  file's own self-reference entry states, applied here to the sibling
  declaration this item's own checkpoint edits." Bundled into CP1's new
  first step alongside B1/B2's other widenings.
- **Missing tests (accepted)** — confirmed directly: `any_protected_path_dirty`
  (`scripts/workflow_state.py:5722-5742`) classifies only `_dirty_paths(repo_root)`
  (`git diff --name-only HEAD` plus untracked, `:5708-5719`) — the
  uncommitted state, not the changed-since-`base_commit` state. Both paths
  B2 above identifies are *committed* by the time they matter
  (`docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` at the plan-approval commit,
  `docs/ACTIVE_MILESTONE.md` at each checkpoint commit), so revision 3's
  proposed dirty-state test would pass while an under-widened implementation
  bundle still could not be generated — confirmed, the round's diagnosis is
  correct. **Decision, two tests, both bundled into CP1's new first step**:
  1. The proposed active-work-item-scoped test changes from
     `any_protected_path_dirty` (dirty-set) to a changed-set check over
     `base_commit..HEAD`: load
     `load_implementation_stage_classification(repo_root,
     artifacts_path_for_work_item(active_work_item_id))` and classify every
     path in `_changed_tracked_paths_between(repo_root,
     work_item["base_commit"], "HEAD")` via `classify_path_implementation_stage`,
     asserting none raises `UnclassifiedPathError` — the same shape
     `assert_all_changed_paths_classified_commit` already provides at the
     plan stage, applied here at the implementation stage against the
     *live* active item (this one legitimately reads live `HEAD`, unlike
     B1's five tests above, because it is checking the *currently active*
     item's own still-moving footprint, not a closed item's fixed history).
     Kept in addition to (not instead of) the existing dirty-set
     `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`,
     which remains a real, distinct pre-commit safety gate.
  2. A new, direct test of exactly B1's own failure class — no test
     previously caught it, per the round's own observation — asserting
     `assert_all_changed_paths_classified_commit(repo_root, BASE_COMMIT,
     WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, protected=wf.PLAN_STAGE_PROTECTED,
     excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
     excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES)` raises nothing,
     naming the classification gap directly as its own assertion rather
     than only as a precondition buried inside a digest computation.

All six required acceptance criteria from round 3's feedback (criteria 1-5,
each addressed by name above, plus criterion 6's disposition requirement,
applied to every Blocking/Important/Optional/missing-tests finding above,
none rejected) are satisfied by the above; see the "Design", "Shared
wiring", and "Checkpoint registry" sections below for the corrected text
itself.

## Revision 5 — `local_model_plan_review` round 4 disposition

Round 4 (`local_model_plan_review`, reviewing revision 4's bundle
`9e9cd6cf9aa0905eb08b3803ec37cada2ba0b67b69a36e9047072bb48db70512`) returned
`Status: REVISE` with 2 Blocking, 2 Important, 2 Optional findings and 2
missing-tests items. The round independently re-verified all eight of round
3's dispositions by executing the remedy in a throwaway clone rather than
reading it, confirming every one applied cleanly; both of this round's own
Blocking findings are new defects in *revision 4's own remedy*, not
re-raised findings. Every finding is **accepted**; none is rejected. Each
was independently re-verified against the live repository before being
applied — every cited line number and function signature re-read directly,
and the two Blocking findings' own remedies additionally confirmed by
building a throwaway clone at `27f051eb`, committing this bundle's own five
working-tree files as a simulated plan-approval commit, applying the
revised remedy below verbatim, and running both `_demo_test.py` suites: the
result is a clean `workflow_fingerprint_demo_test.py` (`Ran 15 tests ...
OK (skipped=4)`) and a clean `workflow_state_demo_test.py`
`TestAgainstRealRepository` (`Ran 16 tests ... OK`) once the already-planned
I2 grandfather entry is included alongside this round's own fixes — a
stronger result than the round's own reported `FAILED (failures=1)`, which
had not yet applied that already-accepted, already-scheduled CP1 step. In
finding order:

- **B1 (accepted, sixth test added)** — verified directly:
  `workflow_state_demo_test.py::test_real_implementing_entry_is_reachable_at_current_head`
  (`:556-565`) calls `ws.implementing_entry_reachable(repo_root, work_item,
  work_item["base_commit"])` with no `head=` argument, so it takes the
  live-`"HEAD"` default (`scripts/workflow_state.py:1574-1575`,
  `implementing_entry_reachable(..., head: str = "HEAD")`), whose own last
  line calls `approval_is_current(repo_root, work_item, stage="plan",
  base_commit=base_commit, head=head)` — the identical live-`HEAD`
  plan-stage recompute `test_real_plan_approval_is_current` already needed
  fixing, reached one call deeper. Revision 4's own B1 enumerated five
  tests and missed this sixth one. **Decision**: add
  `test_real_implementing_entry_is_reachable_at_current_head` as a sixth
  substitution alongside "Revision 4" (B1)'s list, in the "Shared
  wiring"/CP1 text below: pass `head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT`
  explicitly to `ws.implementing_entry_reachable(...)` (it already accepts
  `head`, `scripts/workflow_state.py:1574-1575`), the same fixed-anchor
  treatment the other five already get. Also corrected: CP1's own stated
  pre-step baseline for `workflow_state_demo_test.py` must be measured as
  of the plan-approval commit (the point at which CP1 actually runs), not
  the current uncommitted tree — measured directly in the throwaway clone
  above, before this round's own fix: `Ran 16 tests ... FAILED (failures=1,
  errors=2)`, the two errors being
  `test_real_implementing_entry_is_reachable_at_current_head` and
  `test_real_plan_approval_is_current` (both
  `UnclassifiedPathError: docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`), the
  failure being the already-dispositioned I2 trailer lookalike — i.e. `2
  errors + 1 failure`, not the `1 error + 1 failure` the current text
  states (which was only ever true of the pre-approval-commit tree, where
  `test_real_plan_approval_is_current` was the sole failure this defect
  class caused).

- **B2 (accepted, cross-source comparison repaired)** — verified directly:
  "Revision 4" (B1)'s item 4 substitution
  (`test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas`)
  swaps the manifest's own source to
  `compute_review_content_id_implementation_stage_at_commit(repo_root,
  BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, ...)` but leaves the
  test's own verification loop unchanged —
  `real_sha = wf._hash_object(repo_root, entry["path"])`
  (`scripts/workflow_fingerprint_demo_test.py:460`), which reads the
  *live worktree*. Before the swap both sides read the same source by
  construction, so the assertion always held; after the swap `entry["blob"]`
  is fixed at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` while `real_sha` tracks
  whatever is on disk — a genuine cross-source comparison, red the moment
  any manifest member (a `scripts/` or `.claude/commands/` path) differs
  between the two, which CP1's own first edit guarantees (it edits
  `scripts/workflow_state.py`, `scripts/workflow_integration_test.py`, and
  both `_demo_test.py` files themselves). Reproduced in the throwaway clone
  exactly as the round describes (`entry["blob"]` at `27f051eb` vs. the
  live-worktree `real_sha` after simulating CP1's own edit, mismatched).
  **Decision**: add a small test-local helper,
  `_blob_at_commit(repo_root, commit, rel_path)` (`git rev-parse
  f"{commit}:{rel_path}"`, matching the round's own reproduction command),
  to both `_demo_test.py` files, and change this loop's `real_sha` to
  `_blob_at_commit(repo_root, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
  entry["path"])` — keeping both sides of the comparison anchored to the
  same fixed commit, permanently, regardless of what CP1 or any later
  checkpoint edits in the working tree. Re-verified in the same clone,
  with CP1's own edits simulated: the test passes with this repair, fails
  without it — isolating the fix to exactly this loop.

- **I1 (accepted, same repair applied)** — confirmed directly: "Revision 4"
  (B1)'s item 1 substitution (`test_demonstration_against_real_repo`) makes
  the identical swap for the plan-stage manifest and carries the identical
  loop (`scripts/workflow_fingerprint_demo_test.py:93`,
  `real_sha = wf._hash_object(repo_root, entry["path"])`) — the same defect
  as B2, currently latent in this bundle only because none of the five
  plan-stage protected paths happens to be dirty in its own working tree.
  Reproduced the round's own trigger directly (appending a byte to
  `docs/TECHNICAL_DECISIONS.md` turns the assertion red; reverting it turns
  the assertion green again), isolating the cause to exactly this loop.
  **Decision**: apply the identical `_blob_at_commit` repair here too,
  anchored at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` — the same helper B2
  introduces, not a second implementation, since both loops are the same
  shape.

- **I2 (accepted)** — confirmed directly: the classification table in
  "Revision 3"/"Revision 4" above is derived only from what this item's own
  checkpoints write, and re-running it confirms every path it names
  resolves correctly with no unclassified path — but both comparable
  declarations are far wider than that footprint, for a reason each records
  in its own text: `workflow-v2-1-core-artifacts.json`'s
  `implementation_stage` carries 15 `excluded_paths`/12 `excluded_prefixes`
  covering product/repository content a concurrent work item may write
  (confirmed by reading the file directly), and this item's *own*
  `plan_stage` section already carries the identical set with the identical
  per-entry rationale ("concurrent work items ... may write here;
  fail-closed is preserved for any path outside this named set"). **(Revision
  9 correction, round 8 B1: this claim was never actually measured, and it
  was false — this item's own `plan_stage.excluded_paths` at the time
  carried the "concurrent work items … may write here" set, but was still
  missing three entries `workflow-v2-1-core-artifacts.json` covers instead
  via its own `plan_stage.protected_paths`: `docs/TECHNICAL_DECISIONS.md`,
  `docs/ai-workflow/WORKFLOW_V2_PLAN.md`, `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`.
  Measured directly: classifying all 464 tracked files against
  `resolve_plan_stage_metadata(repo_root, "workflow-v2-3")` left exactly
  these three files (plus six pre-existing, shared, deliberately-unfixed
  build/editor-template gaps) `UnclassifiedPathError`. "Revision 9" (B1)
  below adds the three as this item's own `plan_stage.excluded_paths`
  entries — closing the gap this sentence wrongly assumed was already
  closed.)** The
  proposed `implementation_stage` carries none of this: a single concurrent
  write to `app/`, `docs/ROADMAP.md`, `AGENTS.md`,
  `docs/TECHNICAL_DECISIONS.md`, or any of the other named paths/prefixes,
  anywhere in `27f051eb..HEAD`, raises `UnclassifiedPathError` at
  implementation-bundle generation — precisely the failure class this item
  is currently living through with the sibling declaration, self-inflicted
  onto its own. **Decision**: mirror the remaining twelve `excluded_paths`
  and ten `excluded_prefixes` entries `workflow-v2-1-core-artifacts.json`'s
  `implementation_stage` carries (excluding the three — `CLAUDE.md`,
  `docs/ai-workflow/WORKFLOW_STATE.json`, `docs/ACTIVE_MILESTONE.md` — this
  item's own B1/B2 above already add, with their own, item-specific
  rationale) into
  `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s own
  `implementation_stage`, rationale unchanged (mirrored verbatim from that
  file, the same "mirrors `workflow-v2-1-core-artifacts.json`'s identical
  entry/reasoning" shorthand B1/B2 above already use): `excluded_paths`
  gains `.gitignore`, `AGENTS.md`, `README.md`, `docs/PROJECT_BRIEF.md`,
  `docs/DOMAIN_GLOSSARY.md`, `docs/UX_FLOWS.md`, `docs/ROADMAP.md`,
  `docs/ai-workflow/WORKFLOW_CONFIG.json`,
  `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`,
  `docs/ai-workflow/WORKFLOW_V2_PLAN.md`,
  `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`, `docs/TECHNICAL_DECISIONS.md`;
  `excluded_prefixes` gains `docs/ai-workflow/archive/`, `docs/milestones/`,
  `docs/adr/`, `docs/agent-context/`, `.github/`, `docs/ai-workflow/dry-run/`,
  `docs/improvements/`, `app/`, `config/`, `gradle/`. Bundled into CP1's
  same first step as B1/B2/O2's other widenings.

- **O1 (accepted)** — re-read `WORKFLOW_V2_PLAN.md:30442-30452` directly: it
  grounds `/apply-functional-review`'s bounded-fix branch's `bundle`
  declaration in literal disjunct (C) membership ("that reason is false for
  its bounded-fix branch, **which generates a bundle and records it**") — a
  fact about that branch specifically, not about marker consultation in
  general. `/review-functional` generates no bundle, so this precedent does
  not actually transfer to it, and the comparison clause overstates the
  connection between the two. **Decision**: drop the
  `/apply-functional-review` comparison clause from the `/review-functional`
  "Design" section's `review-subject: bundle` paragraph, the same way
  revision 4 already dropped the `/prepare-review` comparison — the
  marker-is-work-item-keyed argument (`resolve_rejected_marker_path` keyed
  on `work_item_id` alone, confirmed at
  `scripts/workflow_fingerprint.py:1885-1891`) stands on its own and needs
  no borrowed precedent.

- **O2 (accepted)** — re-ran `workflow_fingerprint_demo_test.py` directly:
  `Ran 15 tests ... FAILED (failures=1, errors=3, skipped=4)` — three
  errors and one failure, four failing tests total, matching
  `<bundle_dir>/TEST_RESULTS.md`'s own "Revision 4" section
  (`failures=1, errors=3`) exactly. The Checkpoint registry's CP1 paragraph
  states "4 errors + 1 failure," inherited verbatim from "Revision 3"
  (B2)'s own already-self-contradictory phrasing. **Decision**: correct
  CP1's stated baseline to `failures=1, errors=3` for
  `workflow_fingerprint_demo_test.py`, matching `TEST_RESULTS.md`; the
  `workflow_state_demo_test.py` half of the same sentence is separately
  corrected by B1 above (`2 errors + 1 failure`, measured as of the
  plan-approval commit).

- **Missing tests (accepted, both bundled into CP1's new first step)**:
  1. A static-conformance test asserting every `_demo_test.py` call into
     `approval_is_current`/`implementing_entry_reachable`/
     `compute_review_content_id_*_at_commit*` passes an explicit
     `head=`/`commit=` keyword argument — a cheap textual check that would
     have caught B1's sixth test in seconds, with a named allowlist for the
     one call that is deliberately live-tree
     (`test_real_implementation_stage_classification_has_no_unclassified_dirty_path`'s
     own `load_implementation_stage_classification(repo_root)`/
     `any_protected_path_dirty` call, which takes no such argument at all).
  2. A hermetic regression test, in `scripts/workflow_fingerprint_test.py`
     (using the existing `ScratchRepo` temp-git-repo fixture, not the
     real-repository `_demo_test.py` suite, since this pins the *class* of
     defect B2/I1 fix, not a fact about this repository's own history):
     commit a "protected" file at commit A, then edit that same file's
     working-tree content without committing, and assert that a
     commit-anchored manifest recompute at commit A still reports the
     commit-A blob rather than the dirty worktree content — i.e. that a
     manifest-vs-verification comparison anchored at a fixed commit stays
     immune to concurrent worktree edits, the property B2/I1's repair
     depends on.

All six required acceptance criteria from round 4's feedback (criteria 1-5,
each addressed by name above, plus criterion 6's disposition requirement,
applied to every Blocking/Important/Optional/missing-tests finding above,
none rejected) are satisfied by the above; see the "Design", "Shared
wiring", and "Checkpoint registry" sections below for the corrected text
itself.

## Revision 6 — `local_model_plan_review` round 5 disposition

Round 5 (`local_model_plan_review`, reviewing revision 5's bundle
`d0766688955fa697943d5289184d7e1b24bbbe69a6148f8664eca08ee390cc0f`) returned
`Status: REVISE` with 1 Blocking, 2 Important, 2 Optional findings and 2
missing-tests items. The round independently re-verified all six of
revision 5's dispositions by executing the remedy in a throwaway clone —
built at `27f051eb`, this bundle's own five working-tree files committed as
a simulated plan-approval commit, revision 5's remedy applied verbatim and
committed as a simulated CP1 commit — confirming `workflow_fingerprint_demo_test.py`
goes green (`Ran 15 tests ... OK (skipped=4)`) and the scoped
`workflow_state_demo_test.TestAgainstRealRepository` subset (16 of 42
tests) also goes green, but that the *full* file, over that same clone, is
not. Every finding is **accepted**; none is rejected. This round's own
Blocking finding is a defect the prior three rounds' narrower measurement
scope could not see, not a re-raised finding — confirmed by independently
reproducing the round's own throwaway-clone methodology end to end (not
merely reading its reported numbers), in three stages, per the round's own
required acceptance criterion 5 ("run the gate's own command verbatim, in
full, and report its own output"):

1. **At the simulated plan-approval commit, before any CP1 remedy** — the
   **full** `workflow_state_demo_test.py`: `Ran 42 tests in 87.6s ... FAILED
   (failures=4, errors=3)` — seven failing tests. The three errors:
   `test_real_implementing_entry_is_reachable_at_current_head`,
   `test_real_plan_approval_is_current`, and
   `test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`
   (all three `UnclassifiedPathError: docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`,
   via the identical live-`HEAD` plan-stage recompute chain). The four
   failures: `test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set`
   (the already-dispositioned I2 trailer lookalike, "Revision 3"),
   `test_real_ledger_status_json_now_passes_the_bound_verifier_at_head`, and
   two `subTest`s of
   `test_every_real_evidence_id_the_final_wfo_gate_would_execute_resolves_under_the_pinned_runner`
   (items 352, 359). This is the number CP1's own stated baseline must use,
   since CP1's own verification step runs after this same commit — not the
   `2 errors + 1 failure` `TestAgainstRealRepository`-scoped figure
   "Revision 5" stated.
2. **With revision 5's exact remedy applied verbatim but not this round's
   own seventh-call-site fix, committed as a simulated CP1 commit** — `Ran
   42 tests ... FAILED (failures=2, errors=1)`, reproducing the round's own
   report exactly:
   `test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`'s
   own direct failure, plus the two `TestReconciliationTableLedgerStatusAgreement`
   consequences the round's B1 names (the item-352 `subTest` and
   `test_real_ledger_status_json_now_passes_the_bound_verifier_at_head`).
   This step also confirms two mechanisms directly rather than by
   assertion: `TestCheckpointOriginationAgainstRealRepository`
   (`scripts/workflow_state_demo_test.py:680`, where the seventh call site
   lives) is a different class from `TestAgainstRealRepository` (`:229`) —
   the reason three consecutive `TestAgainstRealRepository`-scoped
   measurements never ran it — and
   `TestReconciliationTableLedgerStatusAgreement`'s
   `_materialize_pinned_worktree_at_commit`/`_run_named_test_in_scratch`
   re-execution genuinely observes **committed** `HEAD` content, not the
   live working tree (a first attempt at this same measurement, before
   committing the test-file fix, reproduced the identical pre-fix failure
   straight from `HEAD`'s still-unpatched committed text — the fix had to
   be committed, not merely edited in the working tree, before the pinned
   worktree observed it, exactly as CP1's own "commit this checkpoint's
   changes, **then** run the full suite" ordering already requires).
3. **With the seventh call site
   (`workflow_state_demo_test.py:857`,
   `test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`)
   also given the identical explicit
   `head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` treatment and committed** —
   `Ran 42 tests ... OK`. Clean: the round's own required fix resolves the
   full baseline with no reintroduced failure.

In finding order:

- **B1 (accepted)** — verified directly, per the measurement above:
  `workflow_state_demo_test.py:857`
  (`TestCheckpointOriginationAgainstRealRepository.test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`)
  calls `ws.implementing_entry_reachable(repo_root, work_item,
  work_item["base_commit"])` with no `head=` argument — the identical call
  shape, and the identical `implementing_entry_reachable` →
  `approval_is_current` →
  `compute_review_content_id_plan_stage_at_commit_for_work_item` →
  `assert_all_changed_paths_classified_commit` → `classify_path` chain,
  "Revision 5" (B1) found one call deeper in
  `test_real_implementing_entry_is_reachable_at_current_head`
  (`scripts/workflow_state.py:1574-1575`, `implementing_entry_reachable(...,
  head: str = "HEAD")`, confirmed by direct reading). Also confirmed
  directly: item 352's bound evidence
  (`docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json`,
  `"evidence": "workflow_state_demo_test.TestCheckpointOriginationAgainstRealRepository.test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit"`)
  is exactly this test, and
  `TestReconciliationTableLedgerStatusAgreement.test_every_real_evidence_id_the_final_wfo_gate_would_execute_resolves_under_the_pinned_runner`
  re-executes it (and every other bound evidence id) under a real pinned
  worktree materialized at live `HEAD` — so leaving this call unfixed would
  leave `workflow-v2-1-core`'s own item-352 ledger evidence failing under
  that pinned re-execution at every subsequent `HEAD`, permanently, not
  merely one more red test inside this item's own window. **Decision**: add
  `test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`
  as a **seventh** substitution alongside "Revision 5" (B1)'s six, in the
  "Shared wiring"/CP1 text below: pass
  `head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` explicitly to
  `ws.implementing_entry_reachable(...)`, the same fixed-anchor treatment
  the other six already get. CP1's own stated pre-step baseline for
  `workflow_state_demo_test.py` is corrected to be measured over the
  **full** file (not the `TestAgainstRealRepository` subset), as of the
  plan-approval commit: `Ran 42 tests ... FAILED (failures=4, errors=3)` —
  seven failing tests (measured directly above), not the `2 errors + 1
  failure` figure "Revision 5" stated.
  `<bundle_dir>/TEST_RESULTS.md`'s "Both suites fully clean" claim
  ("Revision 5" section) is corrected in place (not this plan document) to
  state the narrower scope it actually ran.

- **I1 (accepted)** — confirmed directly:
  `docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json`'s item
  359 binds `test_real_diff_since_base_commit_classifies_exhaustively` as
  its evidence, described there as testing "every tracked-plus-untracked
  path changed since `base_commit`... classifies... without raising
  `UnclassifiedPathError`" — an open-ended, live-working-tree property.
  "Revision 4" (B1)'s item 3 substitution (kept unchanged here) re-anchors
  this same test's scan from
  `wf._changed_tracked_paths(repo_root, BASE_COMMIT) |
  wf._untracked_paths(repo_root)` to
  `wf._changed_tracked_paths_between(repo_root, BASE_COMMIT,
  WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)` — a closed, fixed-history range —
  and no revision through "Revision 5" states this binding or the
  narrowing it causes anywhere in this document (confirmed: `grep -n "item
  359\|fixed-history\|live coverage\|scope-preserving"` over the plan
  before this revision returns nothing). **Decision**: state the binding
  explicitly at "Revision 4" (B1)'s item-3 substitution bullet (Shared
  wiring, below): `test_real_diff_since_base_commit_classifies_exhaustively`
  is `workflow-v2-1-core` item 359's bound evidence; re-anchoring it
  converts that evidence from an open-ended, live-working-tree assertion to
  a fixed-history one (exhaustive classification of `workflow-v2-1-core`'s
  own `162154d3..27f051eb` diff, permanently, unaffected by any later
  commit); live, open-ended changed-set coverage for whichever work item is
  *currently* active moves instead to the new active-work-item-scoped test
  "Revision 4" (missing tests, item 1) already adds to CP1 — a connection
  this document did not previously state.

- **I2 (accepted)** — confirmed directly: `read_manifest_generation_metadata`
  (`scripts/workflow_fingerprint.py:2370-2384`) returns `{}` for a missing
  `MANIFEST.md` (`if not manifest_path.is_file(): return {}`), so
  `assert_local_generation_matches`'s default `require_metadata=False` mode
  (the mode `/review-implementation` step 4 uses) performs **no comparison
  at all** against an absent bundle — it does not raise. The actual failure
  in that same step is `compute_bundle_id`'s own
  `MissingRequiredBundleFileError` (`scripts/workflow_fingerprint.py:264-266`),
  raised when a required bundle file is missing, reproduced directly
  against an empty temp directory. `/review-plan`'s own contract already
  names this refusal class explicitly for the analogous case; `/review-
  implementation`'s steps 3-4 (Design, below) did not. **Decision**: add one
  sentence to step 4 naming the absent/unreadable-bundle case as a clean
  refusal (both the scoped `.ai-review/<work_item_id>/current/` and flat
  `.ai-review/current/` candidate paths named), and correcting the
  assumption that `assert_local_generation_matches` is what detects it — it
  is the `bundle_id`/`review_content_id` recompute at the top of the same
  step that actually fails closed, via `MissingRequiredBundleFileError`.

- **O1 (accepted)** — confirmed directly: after "Revision 4" (O2)'s
  `protected_paths` carve-out, the only remaining edit "Shared wiring"
  states against `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`
  is the single `implementation_stage.excluded_paths` addition of
  `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` (kept solely for
  `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`) —
  confirmed by re-reading the "Shared wiring" prerequisite bullet in full;
  no second edit to that file exists anywhere in this document. **Decision**:
  one clause added to that bullet below, stating plainly that this is the
  one and only edit this item makes to that file.

- **O2 (accepted)** — confirmed directly:
  `TestReviewSubjectDeclarationsLive`'s class docstring
  (`scripts/workflow_state_demo_test.py:1129`) states "the nine-consumer/
  four-exempt split," a second stale count the plan's existing "thirteen" →
  "fourteen" → "fifteen" rename bullet does not track. Both new files are
  `bundle` consumers (never `"none"`), so once both land the real split is
  eleven-consumer/four-exempt, not nine. No test asserts this docstring's
  exact prose (`len(exempt) == 4` is the only live assertion, and it stays
  4 as the plan already states), so this is cosmetic — but in the same
  sentence the rename bullet already touches. **Decision**: the "Deliberate,
  merits-based decisions" bullet (Shared wiring, below) now also updates
  this docstring's count alongside the "thirteen" → "fifteen" rename.

- **Missing tests (accepted, both)**:
  1. Confirmed directly: revision 5's proposed static-conformance test (the
     explicit `head=`/`commit=` scan, with a named allowlist for the one
     deliberately live-tree call) is already specified as a scan over every
     call site in both `_demo_test.py` files, not a hand-enumerated list of
     known offenders — the design itself was already right. What the
     round's own experience actually argues for (a hand-enumerated
     substitution *list*, not the test's own design, coming up short five →
     six → seven, three consecutive rounds) is stated explicitly now rather
     than left implicit: CP1's own gate for this test is the scan-based
     test *passing*, never a hand-checked tally of "all seven known call
     sites are fixed" standing in for it — the whole reason this test earns
     its place is that it does not depend on anyone's enumeration being
     complete, including this document's own.
  2. Confirmed directly: neither CP1's nor CP2's stated verification prose
     names `TestReconciliationTableLedgerStatusAgreement`
     (`scripts/workflow_state_demo_test.py:987`) by name, even though it is
     the one suite that observes a change to bound ledger evidence (B1, I1
     above) and is the slowest single class in the file (measured directly:
     ~50s of the ~88s full-file run). Both checkpoints' "the full existing
     Python suite" gate already includes it — this changes nothing about
     what passes or fails — but naming it explicitly, alongside the two
     `_demo_test.py` files already named, makes the gate visibly
     self-checking rather than incidentally sufficient. **Decision**: both
     named in CP1's and CP2's own verification sentences below.

All six required acceptance criteria from round 5's feedback (criteria 1-5,
each addressed by name above — criterion 5's "run the gate's own command
verbatim, in full, and report its own output" requirement is what produced
the three-stage measurement recorded at the top of this section — plus
criterion 6's disposition requirement, applied to every Blocking/Important/
Optional/missing-tests finding above, none rejected) are satisfied by the
above; see the "Design", "Shared wiring", and "Checkpoint registry" sections
below for the corrected text itself.

## Revision 7 — `local_model_plan_review` round 6 disposition

Round 6 (`local_model_plan_review`, reviewing revision 6's bundle
`9c588460b88cd8f9dd03ded7986abbcaacb0dcf457f93d4b798b58ae9d085a7f`) returned
`Status: REVISE` with 2 Blocking, 1 Important, 1 Optional finding and 2
missing-tests items, plus two non-numbered asks (one in "Migration and
data-integrity concerns," one in "Usability concerns"). The round
independently re-verified all five of revision 6's dispositions
finding-by-finding against the repository before finding new defects; both
of this round's own Blocking findings are new defects in *revision 6's own
remedy* (the static-conformance test's stated property, and the version-
independence claim revision 6 itself did not touch), not re-raised
findings. Every finding is **accepted**; none is rejected. Each was
independently re-verified against the live repository before being
applied — not taken on faith from the feedback's own citations. In finding
order:

- **B1 (accepted, corrected scan property + measured)** — verified
  directly, in three parts, exactly as the round's own three sub-claims
  state:
  1. `workflow_state_demo_test.py:634`-`:639`
     (`TestLegacyImportAgainstRealMilestone8.test_backfilled_review_content_id_reproduces_from_declarations_as_originally_authored`)
     calls `fingerprint.compute_review_content_id_implementation_stage_at_commit(repo_root,
     milestone_8["base_commit"], self.REVIEWED_CONTENT_COMMIT, "product",
     "milestone-8", ...)` — `commit` passed positionally (third argument),
     `REVIEWED_CONTENT_COMMIT` a frozen literal (`"dc4381a3..."`, `:610`),
     no live `HEAD` read. Confirmed against the real function signature
     (`compute_review_content_id_implementation_stage_at_commit(repo_root,
     base, commit, work_item_type, work_item_id, ...)`,
     `scripts/workflow_fingerprint.py:1717-1727` — no keyword-only
     arguments, so positional is legal) that this call is already correct
     and needs no change; it is genuinely an eighth call site into the
     three scanned functions, absent from both the seven-test substitution
     list and any named allowlist.
  2. Confirmed both cited post-remedy shapes
     (`compute_review_content_id_plan_stage_at_commit_for_work_item(repo_root,
     work_item_id, commit, *, base=None)`,
     `scripts/workflow_fingerprint.py:1480-1481`, and
     `compute_review_content_id_implementation_stage_at_commit`, above) pass
     `commit` positionally-or-keyword, not keyword-only — so "Revision 4"
     (B1) items 2 and 4's own prescribed shapes (`base=BASE_COMMIT`/`...`
     with `commit` positional) are themselves legal, non-live calls that the
     stated "explicit `head=`/`commit=` keyword" property would wrongly
     flag.
  3. Re-read `workflow_state_demo_test.py:575-588`
     (`test_real_implementation_stage_classification_has_no_unclassified_dirty_path`)
     directly: it calls only
     `ws.fingerprint.load_implementation_stage_classification(repo_root)`
     and `ws.any_protected_path_dirty(...)` — neither name matches
     `approval_is_current`/`implementing_entry_reachable`/
     `compute_review_content_id_*_at_commit*`, so the named allowlist entry
     for it exempts a call the scan's own function-name scoping could never
     have reached in the first place.

  The round's own diagnosis is correct: the real property CP1's new test
  needs is "never resolves the anchor to live `"HEAD"`/the default," not
  "carries an explicit keyword" — keywordness both over-fires (flags
  `:634`) and under-fires (would pass `head="HEAD"` explicitly, the actual
  defect class). **Decision**: the "Shared wiring" static-conformance test
  bullet is restated with the corrected property (by keyword or position),
  names `:634` explicitly as an eighth, already-conforming, unchanged call
  site (the scan's true scope is eight call sites, not seven), and drops
  the vacuous allowlist entry with no replacement — the corrected property,
  scoped to exactly the three named functions, never reaches either the
  dirty-set test's call (confirmed above) or the new active-work-item-
  scoped test's own deliberately live `_changed_tracked_paths_between(...,
  "HEAD")` call (a different function entirely, never one of the three
  scanned names), so neither needs an exemption.

- **B1's own measurement (accepted, prototyped and pasted)** — the
  corrected scan was written and run against the current tree (both
  `_demo_test.py` files, as they stand at this plan revision, before CP1's
  own substitutions land):

  ```
  Total call sites scanned: 4

  OK   workflow_state_demo_test.py:634 compute_review_content_id_implementation_stage_at_commit -- fixed constant (positional): self.REVIEWED_CONTENT_COMMIT
  FLAG workflow_state_demo_test.py:565 implementing_entry_reachable -- omitted -> live default HEAD
  FLAG workflow_state_demo_test.py:572 approval_is_current -- omitted -> live default HEAD
  FLAG workflow_state_demo_test.py:857 implementing_entry_reachable -- omitted -> live default HEAD

  Flagged: 3 / 4
  ```

  Four real call sites exist today: `:634` correctly passes, and `:565`/
  `:572`/`:857` correctly flag (three of the seven planned substitutions —
  the `workflow_state_demo_test.py` ones). The other four substitutions
  (items 1/2/4, which swap toward one of the three scanned functions; item
  3, which swaps `_changed_tracked_paths_between`, outside the scanned set
  entirely) are not yet in scanned-function form at this plan revision, so
  they are correctly invisible to this measurement — they become
  additional `OK` call sites once CP1's own substitutions land (item 3
  never becomes a scanned call site, by design). **Decision**: this output
  is pasted into `<bundle_dir>/TEST_RESULTS.md`'s new "Revision 7" section
  and into "Shared wiring" below, satisfying the round's own "a
  specification is not a measurement" requirement.

- **B2 (accepted)** — verified directly, all three parts:
  `scripts/prepare-ai-review.sh:440-446` writes an implementation-stage
  `MANIFEST.md` only in the `-n "$WORK_ITEM_ID"` branch; the work-item-id
  argument is documented as optional for `implementation`/`post-fix`
  (`docs/ai-workflow/REVIEW_PROTOCOL.md:25-32`, "required when `<stage>` is
  `plan`... remains optional for every other stage"), and
  `.claude/commands/milestone-implement.md:243-244` itself invokes the
  script with `[work_item_id]` in brackets; `REQUIRED_BUNDLE_FILES =
  frozenset({"MANIFEST.md"})` (`scripts/workflow_fingerprint.py:2649`,
  enforced at `:2802-2804`) is unconditional at every stage. So a flat-
  layout implementation/post-fix bundle carries no manifest at all, and
  `/review-implementation`'s own step 4 (as revision 6 left it) refuses
  unconditionally on it — safe, but the plan's own "version-independent by
  design... for a `governing_workflow_version` of `"1"` or `"2.1"` alike"
  claim (step 2, "Independent inspection") does not state that this
  refusal exists or why, and conflates phase reachability (genuinely
  version-independent) with bundle availability (a separate, generation-
  time precondition that a `"1"`-era bundle is least likely to have met).
  Also confirmed the stale-plan-stage-manifest variant:
  `scripts/prepare-ai-review.sh`'s own `GPT-R30-001/002` comment names the
  scoped case explicitly; the same silent-reuse hazard applies to the flat
  layout. **Decision**: step 2 and "Independent inspection" narrow the
  version-independence claim to the phase guard specifically; step 3 states
  the manifest precondition and instructs regenerating scoped on refusal;
  step 4 names the stale-plan-stage-manifest variant as the specific
  reportable cause when a present-but-wrong manifest disagrees with the
  fresh recompute.

- **I1 (accepted)** — confirmed directly:
  `test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas`
  (`scripts/workflow_fingerprint_demo_test.py:422-473`) calls
  `wf.compute_review_content_id_implementation_stage` twice — the manifest
  computation at `:427-432` (`digest`) and the idempotency recompute at
  `:464-469` (`digest2`), feeding `self.assertEqual(digest, digest2)` at
  `:470`. "Revision 4" (B1)'s item-4 substitution bullet (unchanged through
  revision 6) names only the first call. Swapping only `digest`'s source to
  `_at_commit(repo_root, BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
  ...)` while `digest2` stayed worktree-source would reproduce, one loop
  higher, the identical cross-source comparison "Revision 5" (B2/I1) spent
  a round repairing for the manifest-verification loop in the same test —
  red the moment CP1's own edits (which touch `scripts/`) make the
  worktree diverge from `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT`. Confirmed
  undispositioned: no revision through "Revision 6" names `digest2` or
  `:464`. **Decision**: the item-4 substitution bullet in "Shared wiring"
  now states explicitly that both calls take the swap.

- **O1 (accepted)** — confirmed directly: step 4's two citations of
  `/approve-review`/`.claude/commands/approve-review.md` do not support
  either requirement they're attached to. The real grounding is
  `workflow_state.approval_review_content_id` (`scripts/workflow_state.py:1496-1542`):
  its implementation-stage branch (`:1533-1541`) calls
  `compute_review_content_id_implementation_stage_at_commit(repo_root,
  base_commit, head, ...)` and `load_implementation_stage_classification(repo_root,
  artifacts_path)`, and its own docstring (`:1520-1524`) states both real
  callers "resolve it per work item via
  `fingerprint.artifacts_path_for_work_item(work_item_id)`." Confirmed
  `.claude/commands/approve-review.md` itself says "over the working tree"
  at its own step 2 (`:152-153`) and contains no `artifacts_path_for_work_item`
  substring anywhere (`grep` returns nothing) — an implementer who read only
  the cited file would write the worktree-source call step 4 forbids.
  **Decision**: step 4 cites `workflow_state.approval_review_content_id`
  (`workflow_state.py:1533-1541`) and its docstring instead, and notes in
  one clause that `approve-review.md`'s own "over the working tree" prose
  is looser than the code it actually drives — an existing gap this item
  does not take on fixing, the same "inherited hazard, not new here"
  treatment this document already gives other pre-existing gaps.

- **Missing tests (accepted, both)**:
  1. Confirmed directly: as specified, the scan test passes on empty
     output — nothing forces it to ever find a real defect, so a subtly
     broken callee-matcher (attribute vs. name resolution, a missed
     `ws.`/`fingerprint.` prefix, a module-alias change) would pass
     silently, restoring exactly the false confidence B1 above describes.
     **Decision**: the test also asserts it flags a synthetic
     non-conforming fixture call and that its observed real-call-site count
     is non-zero.
  2. Confirmed directly: `review-implementation.md`'s static-conformance
     test class (added by this same checkpoint) already asserts
     `artifacts_path_for_work_item`'s textual presence but has no equivalent
     check for `MissingRequiredBundleFileError`/the absent-bundle refusal
     wording B2 above adds. **Decision**: the same static-conformance test
     class adds the identical textual-presence style of assertion for
     `MissingRequiredBundleFileError`.

- **Non-numbered asks (accepted, both — round 6's own required acceptance
  criterion 5, applied to this round's feedback in turn: every section
  walked, not only the numbered lists)**:
  1. "Migration and data-integrity concerns": the new active-work-item-
     scoped test deliberately reintroduces a live-`HEAD` scan; its stated
     purpose now notes that a future work item's under-declared
     classification turns *this* test red as a signal about whichever item
     is currently active, not about `workflow-v2-3`.
  2. "Usability concerns" (challenge 5, explicitly "not required"): applied
     anyway, since it costs one sentence — "Checkpoint registry"'s CP1
     paragraph now states why the prerequisite step stays inside CP1 rather
     than splitting into its own CP0: the step's own gate is already
     measured and reported explicitly before any command-file work begins,
     which is the concrete benefit a separate checkpoint would add, so
     splitting would cost a registry regeneration and a second
     commit/verification cycle for no gate this document does not already
     get.

All six required acceptance criteria from round 6's feedback (criteria 1-4,
each addressed by name above; criterion 5's "walk every section" requirement
applied above to both non-numbered asks; criterion 6's disposition
requirement, applied to every Blocking/Important/Optional/missing-tests
finding above, none rejected) are satisfied by the above; see the "Design",
"Shared wiring", and "Checkpoint registry" sections below for the corrected
text itself.

## Revision 8 — `local_model_plan_review` round 7 disposition

Round 7 (`local_model_plan_review`, reviewing revision 7's bundle
`ec1d9806859d1f7d29552df2b340ee91fe92b78d679bb88811556f4ace1db9dd`) returned
`Status: REVISE` with 1 Blocking, 2 Important, 1 Optional finding and 2
missing-tests items, plus a provenance note on the bundle first presented
for this round. The round independently re-verified all five of revision
7's own dispositions finding-by-finding against the repository (enumerated
under its criteria 1-5) before finding new defects, and re-implemented B1's
corrected scan property twice — once from the function signatures, once
from the document's own stated emphasis — to find the two implementations
disagree. Every finding is **accepted**; none is rejected. Each was
independently re-verified against the live repository before being
applied. In finding order:

- **Provenance note (acknowledged, no plan-text action required)**: the
  bundle first presented for this round was stale — the plan document had
  been edited (the revision 7 "correction" block at what was then
  `WORKFLOW_V2_3_PLAN.md:2352`) after that bundle's own generation, so its
  `MANIFEST.md`/`REVIEW_REQUEST.md` claimed a `review_content_id` the
  working tree no longer reproduced. The bundle was regenerated before this
  round reviewed anything, per the user's instruction. Recorded as a process
  fact, not a plan defect: the last write before running
  `./scripts/prepare-ai-review.sh` must be the generation itself, never a
  plan edit slipped in after `REVIEW_REQUEST.md` is updated but before the
  script runs.

- **B1 (accepted, anchor resolution rule stated + negative control
  widened per function)** — verified directly, in full: read
  `implementing_entry_reachable(repo_root, work_item, base_commit,
  head="HEAD")` (`scripts/workflow_state.py:1574-1576`) and confirmed its
  positional index-3 anchor against the real call sites —
  `workflow_state_demo_test.py:565` and `:857` both call it as
  `ws.implementing_entry_reachable(repo_root, work_item,
  work_item["base_commit"])`, third positional argument
  `work_item["base_commit"]`, `head` omitted entirely. Confirmed the
  contrasting `_at_commit*` shapes independently too:
  `compute_review_content_id_plan_stage_at_commit(repo_root, base, commit,
  ...)` and `compute_review_content_id_implementation_stage_at_commit(repo_root,
  base, commit, ...)` (`scripts/workflow_fingerprint.py:1421-1424`,
  `:1717-1720`) both anchor `commit` at positional index 2, and
  `approval_is_current(repo_root, work_item, *, stage, base_commit,
  head="HEAD")` (`scripts/workflow_state.py:1545-1547`) makes `head`
  keyword-only, no positional slot at all. A scan built from "the anchor is
  the third argument" — the reading this document's own B1 text states
  twice, correctly, for the `_at_commit*` functions — reads index 2 as the
  anchor uniformly and silently passes `:565`/`:857` (index 2 there is
  `base_commit`, a non-`"HEAD"` expression), reproducing the round's own
  measured `Total scanned: 4 / Flagged: 1` output exactly when implemented
  that way. Also confirmed the round's negative-control gap: a fixture
  written against `approval_is_current` (keyword-only, no positional slot
  to misread) passes all three specified control assertions even when the
  scan is mis-indexed, since that function was never exposed to the
  bug. **Decision**: the static-conformance test bullet in "Shared wiring"
  now states the anchor resolution rule explicitly — `inspect.signature(fn)
  .bind_partial(...)` against the imported callee, never a hand-maintained
  index map — and the negative control requires one anchor-omitted fixture
  per scanned function name (five), not one shared fixture per shape.

- **I1 (accepted)** — confirmed directly:
  `test_142_generalized_resolver_reproduces_the_migrated_digest_not_a_hardcoded_literal`
  (`scripts/workflow_fingerprint_demo_test.py:116-134`) makes two
  worktree-source calls — `compute_review_content_id_plan_stage_for_work_item`
  at `:124` and `compute_review_content_id_plan_stage` (with `plan_revision`
  from `wf.load_plan_revision(repo_root, ...)`, no `at_commit`) at `:128` —
  and compares them with `assertEqual` at `:134`. The item-2 substitution
  bullet named only `:124`'s swap; swapping only that would anchor
  `digest_generalized` at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` while
  `digest_frozen_defaults` (and its `plan_revision`) stayed worktree-source
  — the identical cross-source comparison this document's own
  `_blob_at_commit` repair (revision 5) and this round's own I1-twin fix to
  item 4 (revision 7) each closed one test away from this one. Confirmed
  `compute_review_content_id_plan_stage_at_commit` takes `plan_revision` as
  a plain parameter with no internal resolution
  (`scripts/workflow_fingerprint.py:1421-1430`), so the replacement must
  source it explicitly; confirmed `load_plan_revision` supports
  `at_commit=` for exactly this purpose (`scripts/workflow_fingerprint.py:667-682`).
  **Decision**: the item-2 substitution bullet in "Shared wiring" now states
  both calls take the swap, with `:128`'s `plan_revision` sourced via
  `wf.load_plan_revision(..., at_commit=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)`.

- **I2 (accepted)** — confirmed directly: the "Shared wiring"
  static-conformance bullet stated the post-CP1 scanned scope as both
  "eight" and, separately, "items 1/2/4 become a fifth through seventh
  scannable call site" — inconsistent even before this round, since item
  4's own revision-7 I1 fix already made it contribute two call sites
  (`:427`, `:464`), not one, which the "fifth through seventh" phrasing
  (three additional slots) never accounted for. Recomputed from today's
  measured baseline of four real call sites plus what each item
  contributes: item 1 one, item 2 two (`:124`/`:128`, per I1 above), item 4
  two (`:427`/`:464`) — five new sites, total **nine**, items 1/2/4
  occupying the fifth through ninth positions; item 3 contributes none, by
  design. **Decision**: both statements in the bullet now derive the same
  number, nine, from the same arithmetic (today's four, plus what each item
  contributes), stated once rather than maintained as two separate tallies.

- **O1 (accepted)** — confirmed directly: `assert_local_generation_matches`
  compares `generation_head` to current `HEAD` by exact equality
  (`scripts/workflow_fingerprint.py:2448-2452`), and
  `/milestone-implement`'s generation-record commit lands before generation
  by contract (`.claude/commands/milestone-implement.md:225-233`), so the
  realistic failure at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is a
  concurrent excluded-only commit — exactly what
  `/recover-implementation-provenance` exists to repair, and what
  `/approve-review`'s own step 1 names for the same phase. Also confirmed
  the ordering: step 4 recomputes and reports the `bundle_id`/
  `review_content_id` digest before calling
  `assert_local_generation_matches`, so a drifted case surfaces as a digest
  mismatch before its actual HEAD-difference cause. **Decision**: step 4
  now names the concurrent-commit cause and points at
  `/recover-implementation-provenance`, and states that a digest mismatch
  co-occurring with a HEAD difference is reported as the HEAD difference,
  not as a separate unexplained digest mismatch — the "or" branch of the
  round's own two suggested remedies, chosen over reordering the step to
  avoid restructuring the "recompute fresh, before reporting anything"
  discipline an earlier round (5, I2) established for this same step.

- **Missing tests (accepted, both)**:
  1. Already applied under B1 above: one anchor-omitted fixture per scanned
     function name, five in total, plus the explicit-`head="HEAD"` fixture.
  2. Confirmed directly: the corrected static-conformance scan covers
     `:128` automatically once it is swapped to
     `compute_review_content_id_plan_stage_at_commit` (a scanned name under
     the corrected property), and the hermetic `ScratchRepo` regression
     test (revision 5) already pins the class of defect I1 describes.
     **Decision**: no new test added for I1; this disposition states why,
     so the revision-9 reader (if any) does not add a redundant one.

All six required acceptance criteria from round 7's feedback (criteria 1-2
addressed under B1 above; criterion 3 under I1; criterion 4 under I2;
criterion 5's "walk every section" requirement applied above, including the
provenance note and the missing-tests I1 statement; criterion 6's
disposition requirement, applied to every Blocking/Important/Optional/
missing-tests finding above, none rejected) are satisfied by the above; see
the "Shared wiring" and `/review-implementation` step 4 sections below for
the corrected text itself.

## Revision 9 — `local_model_plan_review` round 8 disposition

Round 8 (`local_model_plan_review`, reviewing revision 8's bundle
`f633b2872640a72664c937a9789b4bf0606f39e8c2eec7b1d29ca9c4f3411a59`) returned
`Status: REVISE` with 1 Blocking, 2 Important, 2 Optional findings and 2
missing-tests items. The round independently re-verified round 7's own six
required acceptance criteria against the repository (all confirmed applied
correctly, none re-raised) before finding a new defect one stage over from
where seven straight rounds had been looking. Every finding is **accepted**;
none is rejected. Each was independently re-verified against the live
repository before being applied (`resolve_plan_stage_metadata`/
`classify_path` run directly against all 464 tracked files, `discover_current_functional_checklist_evidence`'s
real docstring/source read, both stale descriptors located by direct grep,
`_parse_review_subject_declarations`'s single call site confirmed, and
`milestone-implement.md:225-233`'s real contract text read). In finding
order:

- **B1 (accepted)** — verified directly: classifying all 464 tracked files
  against `resolve_plan_stage_metadata(repo_root, "workflow-v2-3")` leaves
  exactly 9 unclassified (`.editorconfig`, `build.gradle.kts`,
  `docs/TECHNICAL_DECISIONS.md`, `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`,
  `docs/ai-workflow/WORKFLOW_V2_PLAN.md`, `gradle.properties`, `gradlew`,
  `gradlew.bat`, `settings.gradle.kts`) — reproducing the round's own
  measurement exactly. The same check against `workflow-v2-1-core`'s own
  `plan_stage` leaves 7 unclassified, and the two items' excluded *sets*
  are byte-identical; the three-file delta (`docs/TECHNICAL_DECISIONS.md`,
  `docs/ai-workflow/WORKFLOW_V2_PLAN.md`,
  `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`) sits entirely on the protected
  side for `workflow-v2-1-core` (confirmed: all three are members of its
  `plan_stage.protected_paths`) and in neither set for this item —
  `generate_artifacts_declarations` inherits the sibling's excluded sets
  verbatim but replaces `protected_paths` with this item's own three
  artifact paths, dropping the three files that were covered by the
  sibling's *protected* set (never its excluded set) into the void. Since
  `compute_review_content_id_plan_stage*` calls
  `assert_all_changed_paths_classified_worktree`/`_commit` before hashing
  anything, over every path changed since `base_commit` (`27f051eb`, which
  is `HEAD`), a single edit to `docs/TECHNICAL_DECISIONS.md` — tracked,
  live, carrying ten open decision rows — would hard-block
  `/review-plan`, `/record-manual-plan-review`, `/approve-review plan`,
  and every future `IMPLEMENTING`-entry recomputation with
  `UnclassifiedPathError`, and the plan asserted twice that this could not
  happen (both sentences corrected below). **Decision**: add the three
  files as exact `excluded_paths` entries to
  `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s **`plan_stage`**
  section (not `implementation_stage` — that widening remains CP1's own
  `IMPLEMENTING`-stage work, untouched here), with mirrored per-entry
  rationale explaining they are the sibling work item's own protected
  design content, not this item's. This changes this item's own plan-stage
  `review_content_id` (measured: `33a98654…` → `2268fdad…` — an isolated
  intermediate measurement of this widening alone, against revision 8's
  plan text, not any published bundle's own identity; **revision 10, round
  9 O3 clarification, resolves `OPUS-R9-O01`**: `2268fdad…` is not
  reproducible from the current repository state and no published revision
  carries it — the published revision-9 `review_content_id`, after every
  revision-9 edit had landed, is `d5152816…`), which is
  exactly why it must land now, as this plan revision, rather than as a
  quiet CP1 edit after approval.

- **B1's false premises corrected (accepted)** — the two sentences
  claiming this item's own `plan_stage` already carried the sibling's
  concurrency-hardening breadth ("Revision 4" (I2) above, and the
  corresponding "Shared wiring" sentence) are corrected in place, with an
  inline citation of what was actually measured, rather than silently
  rewritten; the "Artifacts-declaration template fit" self-review entry
  is likewise corrected to state the plan stage's own measured result (9/464
  unclassified, three on the protected side) instead of the
  footprint-scoped claim "Revision 3" (B1/B2) originally made and no later
  round re-examined until now. The six remaining unclassified build/editor
  files are named as known, shared, deliberately-unfixed template residue
  in both corrected locations — not something this item introduced or
  should take on.

- **Missing test resolved (accepted)** — confirmed directly: the two tests
  "Revision 4" (missing tests) already schedules into CP1's prerequisite
  step are `implementation_stage`-only (the first) and pinned to
  `workflow-v2-1-core`'s own frozen `PLAN_STAGE_*` constants over a closed
  history range (the second) — neither exercises the *active* item's own
  live `plan_stage` declaration, so B1 above is precisely the defect class
  CP1's own new test would have caught, one stage over, and does not.
  **Decision**: "Shared wiring"'s CP1 prerequisite step gains a third,
  symmetric test — resolve `wf.resolve_plan_stage_metadata(repo_root,
  active_work_item_id)` and assert every path in
  `_changed_tracked_paths_between(repo_root, work_item["base_commit"],
  "HEAD")` classifies via `wf.classify_path` without raising — same shape,
  same live-`HEAD`-scoped stated-purpose note as the sibling
  implementation-stage test, and no allowlist entry needed for the same
  reason the sibling test needs none (`_changed_tracked_paths_between` is
  outside the five-function scanned set entirely).

- **I1 (accepted)** — confirmed directly, from
  `discover_current_functional_checklist_evidence`'s own docstring and
  source (`scripts/workflow_state.py`): it raises
  `NonFirstParentFunctionalChecklistEvidenceError` when round-scoped
  evidence commits exist in `base_commit..head` but none sits on `head`'s
  first-parent chain, and neither this plan nor `PLAN.md` mentioned it
  anywhere (zero occurrences of "NonFirstParent" confirmed by grep before
  this revision). `/prepare-functional-review` step 3a calls the same
  function and is equally silent — confirmed by reading it, the only other
  caller in `.claude/commands/` — so this is an inherited gap, not new
  here, and this repository's linear history does not currently trigger
  it. **Decision**: `/review-functional` step 3 now names the exception as
  a clean refusal with its cause, noting the inheritance explicitly, one
  paragraph after the existing two-outcome handling.

- **I2 (accepted)** — confirmed directly, by grep: the static-conformance
  bullet's "the property, scoped to exactly these **three** function
  names" and "finds exactly four real call sites into the **three** named
  functions" both still read "three" while five sibling sentences in the
  same bullet already read "five" (fixed by revision 8's own count
  correction, which fixed the number but left these two scope descriptors
  at their pre-revision-8 value). This is the fifth consecutive round in
  which a hand-maintained descriptor in this exact bullet has been stale.
  **Decision**: both occurrences now read "five" (restated as "three named
  patterns spanning five function names" at first mention, then "five" at
  second mention), matching the rest of the bullet — one vocabulary
  throughout, and (per the round's own suggestion) the bullet's
  specification is now stated as frozen, so a future revision edits only
  its history.

- **O1 (accepted)** — confirmed directly:
  `.claude/commands/milestone-implement.md:225-233` states "This durability
  commit must land *before* generation, never after" — i.e. record-before-generation
  — while `TEST_RESULTS.md`'s revision-8 evidence bullet cited it as the
  "generation-before-record contract," the literal inverse. The plan
  document's own O1 disposition and step 4 text both get the direction
  right; this was a one-word slip in the bundle's evidence trail only.
  **Decision**: `TEST_RESULTS.md`'s revision-8 bullet is corrected in place
  to "record-before-generation," so a later reader checking whether this
  citation was actually verified sees an accurate paraphrase.

- **O2 (accepted)** — confirmed directly: `_parse_review_subject_declarations`
  has exactly one caller, `discover_review_subject_declarations`, which
  iterates `REVIEW_SUBJECT_ROSTER` only (thirteen `.claude/commands/*.md`
  paths) — `docs/ai-workflow/REVIEW_PROTOCOL.md` is never a member and is
  never scanned by any live code path, so the "Shared wiring" instruction's
  stated rationale for never fencing a literal `review-subject:` line
  there was wrong, though the instruction itself is harmless and worth
  keeping. The sibling `assert_local_generation_matches(` constraint in the
  "Design" section is correctly grounded (confirmed:
  `TestAssertLocalGenerationMatchesCallSiteConformance`'s `_CALL_RE`
  genuinely globs `.claude/commands/*.md` for that literal text) and is
  unaffected. **Decision**: restate the `REVIEW_PROTOCOL.md` guidance as a
  house-style choice (prose reads better, and stays future-proof if the
  roster ever widens to include this file) rather than a mechanical
  constraint.

- **Missing tests (accepted, both)**:
  1. Addressed above under "Missing test resolved."
  2. Confirmed directly: I1 is a one-sentence refusal-wording change in a
     command file (no new production logic to cover beyond the existing
     static-conformance textual-presence pattern, which is optional and
     not added here to avoid duplicating `EXPECTED_ASSERTION_COUNT`'s
     existing single-assertion count, the same reasoning "Revision 3"
     (missing tests) already gives for `/review-functional`); I2 is prose
     consistency inside this plan document and needs no test. **Decision**:
     no new test added for either; recorded here so a later revision (if
     any) does not add a redundant one.

- **Non-numbered ask (accepted, criterion 6)**: the step-4 refusal-table
  drafting suggestion under Usability — **rejected as a plan requirement,
  accepted as a documented option for CP1's implementer.** The suggestion
  changes only prose formatting, not command behavior, and every
  individual refusal cause it would reorganize is already correctly stated
  in step 4's prose (confirmed by reading step 4 directly, all five named
  causes present and accurate). Converting it into a plan-mandated
  requirement now would be scope creep against this revision's own aim
  (closing round 8's actual defects), for the same reason "Revision 8"'s
  answer to the plan's own open question 5 gave for not splitting CP1's
  prerequisite step into a separate checkpoint: no additional gate would
  be added, only a formatting preference imposed ahead of the implementer
  actually writing the file. CP1's implementer may apply the table form at
  their own discretion; this plan does not require it.

All seven required acceptance criteria from round 8's feedback (criterion 1
under B1 above; criterion 2 under "B1's false premises corrected"; criterion
3 under "Missing test resolved"; criterion 4 under I1; criterion 5 under
I2; criterion 6's "walk every section" requirement applied above, covering
both Optional findings, the missing-tests I1/I2 statement, and the
step-4 refusal-table drafting suggestion under Usability; criterion 7's
disposition requirement, applied to every Blocking/Important/Optional/
missing-tests finding above, none rejected) are satisfied by the above; see
the "Shared wiring", `/review-functional` step 3, and "Artifacts-declaration
template fit" sections above/below, and
`docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s `plan_stage`
section itself, for the corrected content.

## Revision 10 — `manual_external_plan_review` round 9 disposition

Round 9 (`manual_external_plan_review`, the first manual-external-stage
round for this item, reviewing revision 9's bundle
`b890d8eaa283a8cc2d5010b49224db134c8c9321e5eb54b02649f241a371dda6`, plan
revision 9, review content ID `d515281615a1f21fbc44ea21b96a82f06339e51162c01d991be676e79166956b`)
returned `Status: REVISE` with 0 Blocking, 2 Important, 4 Optional
findings. Every finding is **accepted**; none is rejected. The round's own
"Independent verification performed" section confirmed bundle-metadata
consistency and re-read the load-bearing command contracts directly rather
than relying on prior disposition history, exactly the standard this
document's own revisions 3-9 already apply. In finding order (original
codes as the round labeled them, mapped to this document's own I/O
numbering):

- **I1 (accepted, `GPT-R9-001`)** — verified directly: the `/review-functional`
  design's step 1 (revision 9 text) both refused an untracked work item and,
  in the same sentence, told the reviewer to read the checklist directly for
  that same case, while step 2's phase guard immediately below requires
  `phase == AWAITING_FUNCTIONAL_REVIEW`, which an untracked item can never
  provide — an implementer would have had to choose between two
  contradictory branches with no stated tiebreaker. **Decision**: mirror
  `/review-implementation` step 1 exactly (single clean refusal, no
  untracked-item branch) — the smallest scope-consistent fix the round
  itself names — applied to the "Design" section's `/review-functional`
  step 1 above. No no-phase branch is specified or wanted for this small
  release.
- **I2 (accepted, `GPT-R9-002`)** — verified directly: both the "Evaluated
  and found justified" paragraph and step 7's report-requirement text
  (revision 9) stated that only the user's manual exercise of "the Android
  app"/"the app" can satisfy the functional-review gate — false for this
  command's own process-work-item callers, `workflow-v2-3` foremost among
  them, whose functional review exercises workflow commands and repository
  state rather than an Android UI (the completed `workflow-v2-1-core`
  functional review is the direct, already-real precedent). **Decision**:
  replace both occurrences with work-item-neutral wording — the user's own
  manual execution of the checklist's required functional flows, naming an
  app walkthrough and command/process checks as the two concrete cases —
  applied to the "Design" section's `/review-functional` justification
  paragraph and step 7 above.
- **O1 (accepted, `GPT-R9-O01`)** — step 3 (revision 9 text) detected a
  live/committed checklist-blob mismatch but then instructed the reviewer to
  continue against the live working-tree text anyway. Not approval-breaking
  as written (the mismatch is reported prominently), but refusing instead is
  simpler and removes any ambiguity about which checklist text the advisory
  opinion actually covers. **Decision**: refuse on a blob mismatch and tell
  the user to rerun `/prepare-functional-review`, applied to the "Design"
  section's `/review-functional` step 3 above.
- **O2 (accepted, `GPT-R9-O02`)** — `/review-implementation` step 4 already
  recomputes the implementation-stage `review_content_id`, but step 6's
  required report shape (revision 9 text) mandated only the protocol's three
  binding fields, not that value. **Decision**: add `Reviewed review content
  ID:` as an additional visible (non-binding, non-parsed) report field,
  applied to the "Design" section's `/review-implementation` step 6 above.
- **O3 (accepted, `OPUS-R9-O01`)** — verified directly: `2268fdad…`, quoted
  in this document's own "Revision 9" (B1) disposition as an intermediate
  measured `review_content_id`, is not reproducible from the current
  repository state and no published revision-9 artifact carries it (it came
  from an isolated measurement of that round's own artifacts-declaration
  widening against revision 8's plan text, taken before every other
  revision-9 edit had landed); the actual published revision-9
  `review_content_id` is `d5152816…`, this round's own "Reviewed review
  content ID". **Decision**: label `2268fdad…` explicitly as an isolated
  intermediate measurement and name the published revision-9 value
  alongside it, applied to "Revision 9" (B1) above. Does not affect any
  implementation contract.
- **O4 (accepted, `OPUS-R9-O02`)** — verified directly:
  `TestAssertLocalGenerationMatchesCallSiteConformance.test_exactly_the_two_live_permissive_callers_exist`
  (`scripts/workflow_integration_test.py:584-600`) scans both
  `.claude/commands/*.md` and `scripts/*.py` (excluding
  `workflow_fingerprint.py` itself and every `*_test.py` module) before
  comparing the combined found set to `EXPECTED_CALL_SITES`, but the
  "Checkpoint registry" section's "Forced update" bullet (revision 2 text)
  described only the command-file scan. **Decision**: correct the
  description to name both scanned globs, applied to the "Checkpoint
  registry" section's "Forced update" bullet above. The required
  implementation action (add `review-implementation.md` to
  `EXPECTED_CALL_SITES`, rename the test/docstring to three) is unchanged —
  descriptive correction only.

**Missing tests (round 9, addressed)**: two new static-conformance
textual-presence checks pin I1/I2, added to CP2's own checkpoint bullet and
the "Self-review" → "Missing tests?" bullet above (required acceptance
criterion 4).

**Required acceptance criteria** (the round's own seven, quoted in its
"Required acceptance criteria" section): criteria 1-3 are satisfied by the
I1/I2 fixes above; criterion 4 by the missing-tests addition above;
criterion 5 (no new lifecycle states, approval paths, ledger fields, or
bundle-identity mechanism) and criterion 6 (existing report-only/no-write/
no-remediation guarantees preserved) are unaffected by any of this round's
fixes — every fix is either a command-contract wording correction or a new
static-conformance test, none touches the state machine, the ledger, or
either command's write set; criterion 7 (re-run the full planned
verification suite after the revision) is satisfied by this revision's own
registry/mapping regeneration and bundle refresh, per
`docs/ai-workflow/MILESTONE_WORKFLOW.md`'s `REVISING_PLAN`/`/apply-plan-review`
mechanics — no product/test code exists yet for either new command (CP1/CP2
are both still unimplemented), so no Python suite run is possible or
required at this stage; CP1's and CP2's own stated verification (full
existing Python suite, zero regressions) remains the gate at
`IMPLEMENTING` time, unchanged by this revision.

## Revision 11 — self-discovered classification-gap fix (not tied to a review round)

While re-verifying revision 10's bundle freshness before another
`local_model_plan_review` round, two new untracked files were found to have
appeared in `docs/ai-workflow/` after revision 10's bundle was generated:
`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` (a 478-line operator
quick-reference for the already-completed Workflow v2.1 command set, "as it
exists today") and `docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg`
(the diagram it embeds). Neither is part of this item's own design surface
(new `/review-implementation`/`/review-functional` commands) or written by
any checkpoint this plan defines; both document the already-`MILESTONE_COMPLETE`
`workflow-v2-1-core` work item's existing behavior, concurrently, in the
working tree. Because neither path was classified in
`docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s `plan_stage` or
`implementation_stage` sections, re-running `scripts/prepare-ai-review.sh`
for this item's plan stage now fails closed with `UnclassifiedPathError` --
and, unresolved, would equally block a future implementation-stage
fingerprint recomputation once CP1/CP2 land, for the same reason B1
(revision 9) fixed an analogous gap for `docs/TECHNICAL_DECISIONS.md`,
`docs/ai-workflow/WORKFLOW_V2_PLAN.md`, and `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`.

**Decision (explicit user direction)**: classify both paths as `excluded`
in both `plan_stage` and `implementation_stage` -- the same
"concurrent, out-of-scope Workflow v2.1 operator documentation" reasoning
already used for `workflow-v2-1-core`'s own protected plan-stage documents
-- without modifying, moving, or deleting the files themselves. Applied to
`docs/ai-workflow/registry/workflow-v2-3-artifacts.json`: the doc path
added to both stages' `excluded_paths`; the diagram directory added to both
stages' `excluded_prefixes` as `docs/ai-workflow/diagrams/`, so any future
file added under it is covered without a further registry edit.

This is not a review-round disposition (no `GPT-R#`/`OPUS-R#` finding code)
-- it is an operator-directed fix to a gap the review process had no way to
anticipate, since the two files did not exist when revision 10's own round
(round 9) was reviewed. It nonetheless changes this item's own plan-stage
`review_content_id`, exactly as B1 did: `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s
own path is itself excluded from the plan-stage content hash (it falls
under the `docs/ai-workflow/registry/` excluded-prefix, like the mapping
ledger), but the *values* of `protected_paths`/`excluded_paths`/
`excluded_prefixes` are themselves part of the hashed projection
(`compute_review_content_id_plan_stage`'s own documented behavior,
`OPUS-R8-014`) -- so this edit changes `review_content_id` even though no
protected file's content changed. This is why it lands as its own plan
revision (11) rather than a quiet edit that would leave the registry's
`plan_revision` field out of step with the digest it actually produces.

No design content changes. `/review-implementation`'s and
`/review-functional`'s own Design-section text (steps, guarantees,
requirements) is unchanged by this revision; only the artifacts-declaration
classification widens, exactly as the B1 precedent already established the
pattern for.

## Revision 12 — round 10 `local_model_plan_review` disposition (1 Blocking, 2 Important, 1 Optional)

Round 10 re-verified revision 11's own six inherited fixes (all confirmed
still applied) and found revision 11's *new* content left `CP1` unable to
reach its own stated verification gate. All four findings share one root
cause: revision 11 widened this item's own
`workflow-v2-3-artifacts.json` for the two newly-discovered files but did
not propagate the consequence into `CP1`'s prescribed edit to
`workflow-v2-1-core-artifacts.json`, into that item's own implementation-
stage `excluded_prefixes`, or into `CP1`'s recorded baselines.

- **B1 (accepted)** — confirmed directly: `workflow-v2-1-core-artifacts.json`'s
  `implementation_stage` did not classify
  `docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` or
  `docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg` (only
  `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` was declared, and that entry
  itself stops covering anything dirty once the plan-approval commit lands,
  per `resolve_plan_stage_approval_commit_paths`). Measured: both real-
  repository suites reproduce exactly the failure/error counts B1 reports
  (`failures=3, errors=3, skipped=4` and `failures=1, errors=1`
  respectively). **Decision**: "Shared wiring"'s `CP1` prerequisite bullet
  and `CP1`'s own checkpoint-registry paragraph both now widen
  `workflow-v2-1-core-artifacts.json`'s `implementation_stage` with three
  entries — the pre-existing `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`
  `excluded_paths` entry plus a new `excluded_paths` entry for
  `docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` and a new
  `excluded_prefixes` entry for `docs/ai-workflow/diagrams/` — restating
  "the one and only edit this item makes to that file" in terms of the
  file as a whole. This widening is *not* applied now, in this plan
  revision: `workflow-v2-1-core-artifacts.json` is a `MILESTONE_COMPLETE`
  work item's own `technical_approval`-protected declarations file, and
  this item's own plan explicitly reserves editing it to `CP1`'s own
  commit (the "one and only edit" framing exists precisely so no earlier,
  unreviewed widening of another item's declarations file happens
  silently, per `implementation_stage.protected_paths`' own rationale,
  `OPUS-R25-006`/`OPUS-R26-004`) — the fix here is to the plan text that
  governs what `CP1` does, not to the file itself.
- **I1 (accepted)** — confirmed directly: `workflow-v2-3-artifacts.json`'s
  own `implementation_stage.excluded_prefixes` was empty; the diagram was
  declared only as a single `excluded_paths` exact-path entry, not the
  `excluded_prefixes` entry the plan text and `TEST_RESULTS.md` both
  claimed. Unlike `workflow-v2-1-core-artifacts.json` above, this item's
  own `workflow-v2-3-artifacts.json` is this item's own live plan
  scaffolding — the same file "Revision 11" itself edited directly,
  outside any checkpoint — so this correction is applied now, directly:
  the exact-path `excluded_paths` entry is replaced with an
  `excluded_prefixes` entry for `docs/ai-workflow/diagrams/`, matching
  `plan_stage`'s existing treatment of the same path and giving the
  future-file coverage the prose already claimed.
- **I2 (accepted)** — confirmed directly, both suites re-run at this
  revision's own `HEAD` (`27f051eba897d77c742ead8b160ed519c0671ee4`):
  `workflow_fingerprint_demo_test.py` reports `failures=3, errors=3,
  skipped=4`, not the recorded `failures=1, errors=3`;
  `workflow_state_demo_test.py` reports `failures=1, errors=1`, not
  `failures=4, errors=3`/"seven failing tests". Three of the seven
  re-anchored tests already pass today because `workflow-v2-1-core`
  reached `MILESTONE_COMPLETE` since the figure was recorded. **Decision**:
  `CP1`'s checkpoint-registry paragraph restates the baseline with the
  re-measured figures, names exactly which failures/errors B1's widening
  and the existing grandfather entry each resolve, and requires
  re-measurement at the actual plan-approval commit if it differs from
  this revision's `HEAD` — a checkable delta instead of an unqualified
  "green"/"seven originally-failing" claim.
- **O1 (accepted)** — folded into B1's disposition above: the "kept
  solely for ..." rationale is now stated in terms of which of the three
  widened paths are actually dirty at `CP1` time (the two "Revision 11"
  files, not `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`, which clears itself
  via the plan-approval commit) rather than left pointing only at the path
  that no longer is.
- **Missing tests (both accepted)** — item 1: `CP1`'s own restated baseline
  paragraph (above) now names
  `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`
  explicitly as one of the two failures B1's widening must clear, in the
  same paragraph that measures the suite baselines, rather than leaving it
  implicit in an aggregate "green" claim. Item 2: resolved via I1's direct
  fix (the `excluded_prefixes` entry) — the new active-work-item-scoped
  implementation-stage classification test `CP1` already adds (per
  "Revision 9" missing tests) covers the regression this gives; no
  additional test is needed.

No design content changes. This revision only corrects `CP1`'s prescribed
edit set and its recorded baselines to match measurement, and fixes this
item's own live `implementation_stage.excluded_prefixes` declaration
directly.

## Revision 13 — round 11 `local_model_plan_review` disposition (0
## Blocking, 1 Important, 2 Optional)

Round 11 re-verified all four of round 10's own findings by independently
reproducing every one — recomputing both identifiers, re-running all eight
suites, simulating `CP1`'s widening in memory, and re-deriving the
re-anchored plan-stage identity at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` —
and confirmed each holds exactly as revision 12 recorded it. It then found
one new defect, of the same class the last two rounds have each found once:
a gap between what `CP1`'s own restated text asserts and what a commit this
item does not itself write can do before `CP1` ever runs.

- **I1 (accepted)** — confirmed directly:
  `test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set`
  is not one of the seven tests re-anchored to
  `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` — it scans `BASE_COMMIT..HEAD` at
  live `HEAD` and asserts set **equality** against the grandfathered set
  (`scripts/workflow_state_demo_test.py:259-281`), so every commit landing
  between now and `CP1` is in its scope, including the plan-approval
  commit itself. That commit is written by `/approve-review` step 6.4,
  which — measured directly — carries no "trailers must be the commit
  message's final paragraph" sentence, unlike the identical sentence
  `.claude/commands/milestone-implement.md:176-181` and
  `.claude/commands/bootstrap-workflow-v2.md:181-186` both already state
  explicitly. The hazard is not theoretical: 41 of the 211 commits in
  `162154d3..HEAD` violate the property (measured), this item's own
  `base_commit` (`27f051eb`, written by `/accept-milestone`, also without
  the sentence) is one of them, and at least one prior *plan-approval*
  commit (`c132185f…`) is another. **Decision**: the plan cannot pin a SHA
  that does not exist yet, so `CP1`'s grandfather entry now states that the
  set is re-derived at the actual plan-approval commit — re-scan
  `162154d3..<that commit>` before `CP1`'s own verification step, and
  grandfather any new violation found there too — rather than presenting
  `27f051eb` as the whole expected set. `/approve-review` itself is left
  unchanged: the re-derivation resolves the hazard without touching a
  fourth file, keeping this item's footprint exactly what "Revision 6" (O1)
  already fixed it at (one and only edit to `workflow-v2-1-core-artifacts.json`,
  no edit at all to `/approve-review`).
- **O1 (accepted)** — confirmed directly: the paragraph named eight
  failures/errors by name (three errors plus three subTest failures in
  `workflow_fingerprint_demo_test.py`, one error plus one failure in
  `workflow_state_demo_test.py`) while its own pass condition said "six,"
  and its re-measurement was framed as conditional ("if the actual
  plan-approval commit differs...") on a condition the same paragraph's own
  prior sentence states is always true (the plan-approval commit is by
  construction a new commit on top of `27f051eb`). **Decision**: the pass
  condition now says "six failing tests (eight reported failures/errors)"
  and requires the re-measurement unconditionally, folding in I1's own
  trailer-lookalike re-scan requirement in the same sentence.
- **O2 (accepted)** — confirmed directly: both the "Shared wiring"
  Prerequisite bullet and the checkpoint-registry paragraph enumerate this
  item's own `workflow-v2-3-artifacts.json` widening entry by entry without
  mentioning the one `excluded_paths` entry
  (`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`) and one
  `excluded_prefixes` entry (`docs/ai-workflow/diagrams/`) "Revision 11" and
  "Revision 12" (I1) already wrote into that same live file — verified
  directly against the file (`excluded_paths`/`excluded_prefixes` each
  carry exactly that one entry today). **Decision**: one clause added to
  each bullet noting both entries are already present and carried forward,
  not additions the bullet itself makes — closing the same propagation gap
  round 10 closed for `workflow-v2-1-core-artifacts.json`, this time for
  this item's own file.
- **Missing tests**: none new — I1's fix is a plan-text statement (the test
  it concerns already exists and already runs at live `HEAD`); O1/O2 are
  wording and enumeration corrections with no testable surface of their
  own.

Independently re-measured, both suites unchanged from revision 12 (no
source/script edit — plan-text only): `workflow_fingerprint_demo_test.py`
`Ran 15 tests ... FAILED (failures=3, errors=3, skipped=4)`;
`workflow_state_demo_test.py` `Ran 42 tests in 89.2s ... FAILED
(failures=1, errors=1)`. The trailer-lookalike observed set, re-scanned in
full over `162154d3..HEAD`: 211 commits, 41 observed violations, exactly
one (`27f051eb`) outside the grandfathered set, zero healed — reproducing
round 11's own measurement exactly. See `TEST_RESULTS.md`'s "Revision 13"
section for the full re-run record.

No design content changes. This revision only restates `CP1`'s
trailer-lookalike grandfather entry and pass condition to survive a commit
this item does not itself write, and completes the two enumerations O2
found incomplete.

## Naming note (explicit user decision, recorded here)

`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s "Deferred to Workflow v2.2" section
already reserves the name "Workflow v2.2" for a different, unstarted scope
(metrics/hooks/status-line instrumentation, context-governance thresholds,
subagent-routing experiments) deferred from Workflow v2.1. This work item's
own scope — two model-independent, report-only reviewer commands — is
unrelated to that reservation. Rather than silently repurpose the "v2.2"
name (work-item ids are permanently non-reusable once created,
`D-Checkpoint-Ownership`/`WFR-66`), the user was asked directly and chose to
keep "Workflow v2.2" reserved for the original instrumentation scope and
give this work a different id. This item is therefore `workflow-v2-3`, not
`workflow-v2-2`. No other document is changed by this decision —
`WORKFLOW_V2_PLAN.md` belongs to the closed `workflow-v2-1-core` work item
and is not touched here.

## Scope note

This is an **operator-ergonomics** release: two new, model-independent,
report-only reviewer commands, reusing the existing Workflow v2.1 lifecycle
and review states verbatim. It adds no new lifecycle state, no new ledger
field, and no new bundle-identity concept. Richer local-vs-external reviewer
orchestration (e.g. a recorded local-review ledger stage for the
implementation/functional gates, analogous to the plan stage's two-stage
protocol) and controller-aware lifecycle states are explicitly out of scope
here and deferred to Workflow v3 (see "Deferred to Workflow v3" below).

## Requirements (from the user's brief, verbatim scope)

1. Add a model-independent `/review-implementation <work-item-id>` command,
   analogous in philosophy to `/review-plan`.
2. Evaluate, and — if justified — add a model-independent
   `/review-functional <work-item-id>` command for the same reviewer-side
   symmetry.
3. Review commands must work with any capable model; never hard-code
   Opus/Sonnet/GPT/another provider or model.
4. Do not add `AWAITING_LOCAL_*`, `AWAITING_INTERNAL_*`, `AWAITING_MODEL_*`,
   or equivalent new lifecycle states in this release.
5. Reuse the existing review states: `/review-implementation` operates
   while the item remains in `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`;
   `/review-functional` operates while it remains in
   `AWAITING_FUNCTIONAL_REVIEW`.
6. Reviewer commands must review and report only — never modify
   implementation/source/test content, approve the stage, apply findings,
   or advance workflow state.
7. Preserve the existing apply commands
   (`/apply-plan-review`/`/apply-implementation-review`/
   `/apply-functional-review`) as the sole authoritative remediation paths.
8. Keep the change small and ergonomic.

## Independent inspection — what the current Workflow v2.1 implementation
## actually does (relevant findings)

Read in full before drafting this plan: `docs/ai-workflow/MILESTONE_WORKFLOW.md`,
`docs/ai-workflow/REVIEW_PROTOCOL.md`, `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`,
and every file in `.claude/commands/` (`review-plan.md` most closely, as the
philosophical template; `apply-plan-review.md`, `apply-implementation-review.md`,
`apply-functional-review.md`, `record-manual-plan-review.md`,
`prepare-functional-review.md`, `approve-review.md`, `prepare-review.md`,
`accept-milestone.md`). Also read the relevant Python surface in
`scripts/workflow_fingerprint.py`/`scripts/workflow_state.py` (signatures and
docstrings only, not the full 3.6k/11k-line files) and the closed-roster
conformance tests in `scripts/workflow_integration_test.py`/
`scripts/workflow_state_demo_test.py` that enumerate `.claude/commands/*.md`
files by name.

Key findings that shape this plan:

- **`/review-plan`'s philosophy**: a *role*, not a model — "Running it from
  any capable Claude model produces the same schema and the same
  transition." Resolves the work item (arg or `active_work_item_id`),
  guards `governing_workflow_version`/`phase`, reads the bundle, recomputes
  `bundle_id`/`review_content_id` fresh (never trusts stale `MANIFEST.md`
  claims), checks worktree/HEAD staleness
  (`assert_local_generation_matches`) and REJECTED-bundle status
  (`assert_bundle_not_rejected`), independently re-verifies every claim
  against the real repository (never takes the plan doc's disposition table
  on faith), writes `REVIEW_FEEDBACK.md` plus a ledger entry, and — critically
  — **stops**, reporting the exact next command, never auto-continuing.
  This plan's two new commands copy that same operational discipline
  (independent re-verification, fresh recomputation, explicit stop) but
  **not** its ledger-writing/phase-transitioning half — requirement 6 rules
  that out for this release.
- **The two-stage local-then-manual-external plan-review protocol
  (`AWAITING_LOCAL_PLAN_REVIEW`/`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`,
  `D-Plan-Review-Stages`) is scoped entirely to `governing_workflow_version:
  "2.1"` and to the *plan* stage only.** It is the one place Workflow v2.1
  actually did add new lifecycle states for a local-reviewer role — and
  requirement 4 explicitly forbids repeating that pattern here. This plan
  does not touch that protocol, its ledger (`plan_review_stages`), or its
  states at all.
- **`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` and `AWAITING_FUNCTIONAL_REVIEW`
  are version-uniform** — `docs/ai-workflow/MILESTONE_WORKFLOW.md` defines
  both identically regardless of `governing_workflow_version`. Unlike
  `/review-plan`/`/record-manual-plan-review` (which refuse cleanly for a
  `"1"` item, since the two-stage plan protocol they belong to is
  `"2.1"`-only), the two commands this plan adds have their *phase guard*
  reachable for **either** governing version — a real, and simpler, point
  of divergence from `/review-plan`'s own contract. **Scope correction
  (revision 7, round 6 B2)**: this version-independence is specifically
  about the phase guard, not a claim that a usable bundle is always present
  once it passes. Bundle availability (an implementation-stage
  `MANIFEST.md` existing at all) is a separate, generation-time
  precondition orthogonal to `governing_workflow_version` — it depends on
  whether the optional work-item-id argument was passed to
  `prepare-ai-review.sh`, not on which workflow version governs the item;
  see `/review-implementation`'s steps 2-4 below for the corrected claim
  and the refusal this produces when the precondition isn't met. This
  precondition doesn't arise for `/review-functional`, which never touches
  a bundle at all (see below).
- **Functional review has no bundle, but the `REJECTED` marker still
  applies.** `/prepare-functional-review` never calls
  `scripts/prepare-ai-review.sh` at all — it writes the checklist directly
  into `docs/ACTIVE_MILESTONE.md` and pins it with a dedicated
  checklist-evidence provenance commit
  (`workflow_state.discover_current_functional_checklist_evidence`,
  keyed by commit SHA + blob, not `bundle_id`/`review_content_id`). There is
  no `MANIFEST.md` and no `bundle_id` at this stage in the normal
  milestone-gated flow. **Revision 2 correction**: revision 1 additionally
  claimed there is no `REJECTED`-bundle marker concept at this stage,
  citing `assert_bundle_not_rejected`'s own docstring as enumerating no
  functional-review-stage consumer — that claim was wrong (the docstring
  already names "`/apply-functional-review`'s bounded-fix branch", and
  `resolve_rejected_marker_path` is keyed on `work_item_id` alone, not on
  any bundle, so the marker is live at every phase, functional review
  included). See "Design" below and the revision 2 disposition above (B2)
  for the corrected behavior. `/review-functional` is still **not** a
  bundle reviewer the way `/review-implementation` is in every other
  respect — it reviews the checklist text plus the reproducibility of its
  cited automated evidence, using the checklist-evidence lookup instead of
  a bundle recompute; only the `REJECTED`-marker check is shared with the
  bundle-bearing stages.
- **Reuse, not new code, for almost everything needed.** Every primitive
  these two commands need already exists and is already stage-generic:
  `resolve_bundle_dir`/`resolve_feedback_dir`,
  `compute_bundle_id`/`compute_review_content_id_implementation_stage`,
  `assert_local_generation_matches`, `assert_bundle_not_rejected`, and
  (for the functional command)
  `discover_current_functional_checklist_evidence`. No new function is
  needed in `scripts/workflow_fingerprint.py` or `scripts/workflow_state.py`
  beyond one small, mechanical registration (below). This is what makes the
  "smallest coherent plan" claim real rather than aspirational.
- **Three closed-roster registrations are relevant; only one is
  structurally forced (revision 2 correction — I1)**:
  1. `scripts/workflow_state.py`'s `REVIEW_SUBJECT_ROSTER` (a hardcoded
     `frozenset` of exactly the `.claude/commands/*.md` paths that must
     declare a `review-subject:` frontmatter value) — currently 13 entries.
     `discover_review_subject_declarations` iterates this roster only, so a
     file left off it is simply invisible to
     `scripts/workflow_state_demo_test.py::TestReviewSubjectDeclarationsLive` —
     **not forced**. Both new files are still added to the roster and to
     that test's `EXPECTED`/`EXPECTED_ASSERTION_COUNT` dicts, but as a
     deliberate decision justified on the merits (both genuinely satisfy the
     roster's own inclusion criteria — see the revision 2 disposition above,
     I1), not as mechanical necessity — unlike `.claude/commands/recover-
     implementation-provenance.md`, added after `WFR-67`'s design was frozen
     and deliberately left off the roster as an acknowledged, pinned gap.
     The test's own "thirteen" naming still must track the real count as
     each command lands.
  2. `scripts/workflow_integration_test.py::TestAssertLocalGenerationMatchesCallSiteConformance`
     glob-scans **every** `.claude/commands/*.md` file (not a fixed list)
     for `assert_local_generation_matches(` call sites and asserts the
     found set equals exactly `EXPECTED_CALL_SITES` (today: `approve-review.md`,
     `review-plan.md`) — this one **is forced**: since `/review-implementation`
     needs this same worktree/HEAD staleness check (it is a repository-local
     command, exactly like `/review-plan`), `review-implementation.md` must
     be added to `EXPECTED_CALL_SITES` in the same checkpoint that creates
     it, or the hermetic suite (which runs in the ordinary test commands)
     fails immediately.
  3. `scripts/workflow_integration_test.py::TestGoldenCommandFileHashes`
     (`_GOLDEN_COMMAND_FILE_SHA256`) iterates its own dict only, so it is
     **not forced** either — both new files still get an entry, computed
     once their content is finalized, matching how this milestone's history
     already treats every other modified command file.
- **No "Open decision" row in `docs/TECHNICAL_DECISIONS.md` is touched** —
  confirmed by reading that file's "Open decisions" table in full; all ten
  rows are product/domain decisions (Gradle module split, Room schema,
  progression formulas, navigation, backup location/retention, notification
  permission behavior, cross-day workouts), none implicated by this
  process-tooling change.
- **The default plan-stage/implementation-stage path-classification
  template** (`workflow_state.generate_artifacts_declarations`, written to
  `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`) is a shared
  literal default whose human-readable rationale strings still narrate
  `workflow-v2-1-core`'s own origin checkpoints (e.g. "WF4a-ii", "the eight
  existing v1 commands", "the bootstrap command") — confirmed by generating
  it for real and reading the output. The **plan-stage** path/prefix
  classification is functionally correct and complete for this item's own
  footprint: every path this plan's checkpoints touch is already covered by
  an existing excluded prefix/path there. **Revision 3 correction (round 2,
  B1)**: the parallel claim this section previously made about the
  **implementation stage** was wrong — "the implementation stage's default
  (nothing pre-excluded) is exactly right" confused "nothing pre-excluded"
  with "everything protected"; `classify_path_implementation_stage`
  (`scripts/workflow_fingerprint.py:1568-1593`) has no default branch and
  fails closed (`UnclassifiedPathError`) on any path that is neither
  protected nor excluded, and the generated declaration's implementation
  stage carries only a self-reference `protected_paths` entry — every other
  path this item's own footprint touches is unclassified. See "Revision 3"
  above (B1) for the measured failure and "Revision 4" above (B1/B2) for
  the corrected, explicit widening — including this item's own plan
  document and `docs/ACTIVE_MILESTONE.md`, both missing from the footprint
  as revision 3 stated it — CP1 now performs before any implementation
  bundle is generated. Generalizing the template's own rationale prose
  remains out of scope for
  this small, ergonomics-focused item (it would be a change to shared,
  cross-work-item tooling code, not to this item's own deliverable).

## Design

### `/review-implementation <work-item-id>` (new)

`review-subject: bundle`, `state_writer: false`. Enters no new state — it is
an optional, non-gating action available while the resolved item's `phase`
is exactly `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
(`docs/ai-workflow/MILESTONE_WORKFLOW.md`). Implements a model-independent
**review role**, not a specific model: nothing in this contract, in the
report it produces, or in any check it performs names a model — running it
from any capable Claude model produces the same behavior.

**Review and report only.** This command never writes
`<feedback_dir>/REVIEW_FEEDBACK.md`, never writes
`docs/ai-workflow/WORKFLOW_STATE.json`, never edits source/test/doc content,
never approves a stage, and never advances `phase`. `/apply-implementation-review`
remains the sole authoritative path for validating and applying real
implementation-review feedback, and `/approve-review implementation`
remains the sole, user-only approval gate — both entirely unchanged and
unaffected by this command's existence.

Steps:

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
   for both. **Scope correction (revision 7, round 6 B2)**: that
   version-independence claim covers only this phase guard. Whether a
   usable bundle actually exists once the guard passes is a separate,
   generation-time precondition — orthogonal to `governing_workflow_version`
   and not implied by it — see step 3/4's manifest precondition below.
3. **Read**: `<bundle_dir>/REVIEW_REQUEST.md`, `IMPLEMENTATION_SUMMARY.md`,
   `TEST_RESULTS.md`, `CHANGED_FILES.txt`, `COMMITS.txt`, `DIFF.patch`,
   `files/`, `MANIFEST.md`, the required-context file list, and any prior
   `<feedback_dir>/REVIEW_FEEDBACK.md` for continuity across rounds.
   `<bundle_dir>`/`<feedback_dir>` resolve per
   `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
   (`workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir`). An
   implementation-stage bundle generated without the plan stage's mandatory
   work-item-id argument lands in the flat `.ai-review/current/` path while
   the resolver still prefers a scoped `.ai-review/<work_item_id>/current/`
   directory if one exists — an inherited hazard, not new here (`/approve-
   review` has the same one); `assert_local_generation_matches` (step 4)
   normally surfaces the resulting staleness, but if it doesn't, report the
   mismatch by naming both candidate paths rather than only the resolved
   one. **Manifest precondition (revision 7, round 6 B2)**: an
   implementation/post-fix bundle generated without that same optional
   work-item-id argument (`./scripts/prepare-ai-review.sh <base-sha>
   implementation`, no third argument — a documented, supported invocation,
   e.g. `.claude/commands/milestone-implement.md`'s own `[work_item_id]`)
   carries **no implementation-stage `MANIFEST.md` at all**:
   `scripts/prepare-ai-review.sh` writes one only in the
   `-n "$WORK_ITEM_ID"` branch. Step 4's `compute_bundle_id` call fails
   closed on this with `MissingRequiredBundleFileError` — a clean, expected
   refusal for this specific case, not a corrupt-bundle symptom; report it
   as such and tell the user to regenerate scoped
   (`./scripts/prepare-ai-review.sh <base-sha> implementation
   <work_item_id>`).
4. **Recompute fresh, before reporting anything**: an absent or unreadable
   bundle is a clean refusal here, named plainly rather than left to
   surface as a raw traceback (revision 6, round 5 I2) — both candidate
   `<bundle_dir>` paths (`.ai-review/<work_item_id>/current/` and the flat
   `.ai-review/current/`) named in the refusal. Do not rely on step 4's
   later `assert_local_generation_matches` call to catch this case: its
   default `require_metadata=False` mode reads `MANIFEST.md`'s
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
   branch uses (`scripts/workflow_state.py:1533-1541`; revision 7, round 6
   O1 — cited directly rather than via `/approve-review.md`, below), **never** the worktree-source
   `compute_review_content_id_implementation_stage` (no `head`/`commit`
   parameter at all, scoped instead to whatever is currently dirty), which
   would not reproduce the value `MANIFEST.md` records or
   `/approve-review implementation` itself checks (revision 4, round 3 I1:
   the generator's sole writer,
   `write_manifest_with_verified_identifiers_implementation_stage`, is
   commit-source too, `scripts/workflow_fingerprint.py:3277-3300`) — with
   the resolved item's own four classification mappings loaded through
   `workflow_fingerprint.load_implementation_stage_classification(repo_root,
   artifacts_path=workflow_fingerprint.artifacts_path_for_work_item(work_item_id))`
   — never that function's own default `artifacts_path`, which resolves to
   `workflow-v2-1-core`'s artifacts file and would silently compute a
   different work item's classification (matching
   `workflow_state.approval_review_content_id`'s own usage and its
   docstring's own statement that both real callers "resolve it per work
   item via `fingerprint.artifacts_path_for_work_item(work_item_id)`";
   revision 7, round 6 O1 — **not** `.claude/commands/approve-review.md`,
   which is not where this behavior actually lives: its own step 2 prose
   says the recompute runs "over the working tree" — the opposite of
   commit-source, on its face — and `artifacts_path_for_work_item` appears
   nowhere in that file; that file's own prose is looser than the code it
   drives, an existing gap this item does not take on fixing). Report,
   rather than silently proceeding past, any mismatch against what
   `MANIFEST.md`/`REVIEW_REQUEST.md` claim. **Stale-plan-stage-manifest
   variant (revision 7, round 6 B2, second part)**: if `MANIFEST.md` is
   present and otherwise looks healthy but the recomputed
   implementation-stage `review_content_id` still disagrees with what it
   records, name this specific cause explicitly — an unscoped
   implementation/post-fix bundle written into a directory that already
   holds a plan-stage `MANIFEST.md` silently reuses that plan-stage
   manifest (`scripts/prepare-ai-review.sh`'s own `GPT-R30-001/002` comment
   names this for the scoped case; the same hazard applies to the flat
   layout), so the mismatch is comparing today's implementation-stage
   recompute against a stale plan-stage identity, not a real content
   discrepancy — report it as that, not as an unexplained digest mismatch.
   This command runs inside a real, current worktree, so also call
   `workflow_fingerprint.assert_local_generation_matches(repo_root,
   <bundle_dir>/MANIFEST.md)` and stop, naming both the recorded and current
   worktree_root/HEAD, on a `WorktreeOrHeadMismatchError` — the same
   repository-local staleness discipline `/approve-review`/`/review-plan`
   already apply. **Dominant cause at this phase (revision 8, round 7 O1)**:
   at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `generation_head` sits at
   `/milestone-implement`'s own generation-record commit, which lands
   *before* generation by contract
   (`.claude/commands/milestone-implement.md:225-233`), so a healthy bundle
   satisfies `generation_head == HEAD` exactly; the realistic way this check
   fails here is a concurrent excluded-only commit landing after generation
   — the case `/recover-implementation-provenance` exists to repair, and the
   same cause `/approve-review`'s own step 1 names for this phase. Report a
   `WorktreeOrHeadMismatchError` naming that cause and pointing at
   `/recover-implementation-provenance`, not only the raw recorded/current
   values. Because this check runs after the digest recompute above, that
   concurrent-commit case surfaces first as a `bundle_id`/`review_content_id`
   mismatch and only second as the HEAD difference that actually explains
   it — when both are present, report the HEAD difference as the cause, not
   the digest mismatch as a separate, unexplained one. **`REJECTED`-bundle refusal, this command's sole
   assertion, immediately preceding the report** (`WFR-67`; for a
   report-only consuming act, this single assertion is also the
   mutation-guard assertion, the same pattern `/prepare-review`/
   `/milestone-implement`/`/milestone-plan` already use): call
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
6. **Compose the report** in exactly `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
   `REVIEW_FEEDBACK.md` structure (`Status: APPROVE | REVISE | BLOCK`,
   Blocking/Important/Optional findings, Missing tests, Architecture and
   maintainability concerns, Migration and data-integrity concerns,
   Usability concerns, Required acceptance criteria), plus the three
   binding fields (`Reviewed bundle ID:`, `Reviewed base commit:`,
   `Work item:`) stated with this invocation's own freshly recomputed
   values — so that if the user chooses to hand-copy this report into
   `<feedback_dir>/REVIEW_FEEDBACK.md` as the authoritative external round,
   it already satisfies `workflow_fingerprint.parse_review_feedback_binding_fields`/
   `assert_feedback_matches_bundle` (`WFR-03`) without further editing.
   **Additional visible field (revision 10, round 9 O2 — resolves
   `GPT-R9-O02`)**: also state `Reviewed review content ID:` with step 4's
   freshly recomputed implementation-stage `review_content_id` — not one of
   the three parsed binding fields, so no parser or approval requirement
   changes; purely so the printed advisory opinion is easy to correlate
   against the exact reviewed implementation content.
7. **Report only — write nothing.** Print the composed report directly in
   this turn's response. State plainly that this is an independent,
   advisory opinion from whichever model ran this command, not a recorded
   review round: `<feedback_dir>/REVIEW_FEEDBACK.md`,
   `docs/ai-workflow/WORKFLOW_STATE.json`, and every other repository file
   are untouched. If the user wants this opinion to become the authoritative
   round, they place it (verbatim, or after obtaining a further external
   reviewer's own separate pass) at `<feedback_dir>/REVIEW_FEEDBACK.md`
   themselves — `/apply-implementation-review` and `/approve-review
   implementation` remain the only commands that ever act on that file.
   Never approve, never apply findings, never transition `phase`, never
   auto-continue to any other command.

Do not implement product or test code in this command. Do not edit the
plan/registry/mapping/artifacts files or any other command file.

### `/review-functional <work-item-id>` (new)

**Evaluated and found justified.** A model can't perform the user's own
manual functional-review pass — only the user can actually exercise the
manual flows `AWAITING_FUNCTIONAL_REVIEW`'s checklist names, whatever those
flows are for the work item at hand: an Android-app walkthrough for
product/UI work, or command/process checks (running workflow commands,
inspecting repository state transitions and evidence, confirming operator
behavior) for a process work item such as `workflow-v2-3` itself
(**work-item-neutral wording, revision 10, round 9 I2 fix — resolves
`GPT-R9-002`**: see "Revision 10" below for why this differs from revision
9's own, product-specific text here; the completed `workflow-v2-1-core`
functional review is the direct precedent for the process-work-item case).
But a model *can* do exactly
what `/review-plan` already does for a plan bundle, adapted to this stage's
different artifact shape: independently re-run every automated-verification
command the checklist cites and confirm the real output, and independently
assess whether the checklist's manual-flow list actually covers the
behavioral surface of the diff since the item's `base_commit` (or since the
prior round's own evidence, for a continued round) — catching a stale
command, a missing setup step, or an uncovered code path *before* the user
spends time on the manual pass, exactly the "second pair of eyes before an
expensive/manual gate" value `/review-plan` already provides for the plan
stage. This is real, distinct signal, not a rubber-stamp duplicate of the
user's own reading — hence justified, at the same small scope as
`/review-implementation`.

`review-subject: bundle`, `state_writer: false`. There is no bundle,
`bundle_id`, or `MANIFEST.md` at this stage in the normal milestone-gated
flow (see "Independent inspection" above), so this command never calls
`assert_local_generation_matches` (no `MANIFEST.md` to compare against —
note for implementers: never write that sentence with a call-shaped
`assert_local_generation_matches(` substring anywhere in this file,
including in prose; `TestAssertLocalGenerationMatchesCallSiteConformance`'s
`_CALL_RE` glob-scans literal text, not semantic call sites) — but it is
**not** `review-subject: none` like `/prepare-functional-review`, because
`resolve_rejected_marker_path(repo_root, work_item_id)` is keyed on
`work_item_id` alone, not on any bundle (`scripts/workflow_fingerprint.py:1885-1891`):
a withdrawn work item carries a live `REJECTED` marker regardless of phase,
including while it sits at `AWAITING_FUNCTIONAL_REVIEW`, and `assert_bundle_
not_rejected`'s own docstring already names "`/apply-functional-review`'s
bounded-fix branch" as a required consumer for exactly this reason. This
command therefore calls `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
work_item_id)` once, immediately before composing its report — a withdrawn
work item must not receive an advisory functional-review opinion either.

**`review-subject: bundle` is a deliberate widening, not a claim of literal
disjunct membership (revision 3 correction, round 2 I1).** `/review-functional`
satisfies none of `WORKFLOW_V2_PLAN.md:30306-30312`'s three semantic
disjuncts as originally defined ((A) reads `REVIEW_FEEDBACK.md`; (B) reads
an artifact under `<bundle_dir>` it did not itself generate; (C) presents
`<bundle_dir>`/the archive as ready for external review) — it touches no
bundle at all. The declaration instead widens what `bundle` answers, from
"can a marked bundle become this command's subject" to "does this command
consult the work-item-scoped `REJECTED` marker" (revision 5 correction,
round 4 O1: stated on its own merits alone now, not by comparison to
`/apply-functional-review`'s own bounded-fix branch — that branch's own
`bundle` declaration rests on literal disjunct (C) membership,
`WORKFLOW_V2_PLAN.md:30442-30452`, "that reason is false for its
bounded-fix branch, **which generates a bundle and records it**", a fact
that does not transfer here, since `/review-functional` generates no
bundle at all). Reconciled explicitly with `/prepare-functional-review`'s
`none`
(`WORKFLOW_V2_PLAN.md:30437-30441`: "`/prepare-functional-review` takes
`FUNCTIONAL_REVIEW.md` as its subject — a user-written checklist that
carries no `Reviewed bundle ID:` and binds to no bundle — so no refused
bundle can reach it at all"), which after this widening is the one command
reachable from `AWAITING_FUNCTIONAL_REVIEW` that does *not* consult the
marker. **(Revision 4 correction, round 3 O1: this is not the same
exemption `/prepare-review`'s generation path gets, and is not described
as such.)** `WORKFLOW_V2_PLAN.md:30428-30436`'s "generator, scoped by act"
exemption is stated on a specific, narrower ground than "is a generator":
"the marker's writer and its only sanctioned clearer; blocking it would
leave a marked work item recoverable only by a hand edit" — a role
`/prepare-functional-review` does not hold, since it neither writes nor
clears the `REJECTED` marker at all. `/prepare-functional-review`'s own
exemption rests on the *different*, subject-based ground the plan already
quotes immediately above: it takes `FUNCTIONAL_REVIEW.md` (a checklist that
binds to no bundle) as its subject, so no refused bundle can reach it, full
stop — the same "no bundle subject" reasoning `/accept-milestone` gets one
sentence later in the same source paragraph. `/review-functional`, by
contrast, is purely a **report** act (it writes nothing) consuming an
*existing* round's evidence — genuinely a different case from either of
those two, justified on its own terms above (the deliberate `bundle`
widening), not by analogy to a differently-grounded exemption. This
asymmetry — one command at this gate consulting the marker, one not — is
stated here rather than left implicit, and flagged as a known input to the
deferred `WF8c` `review-subject:` re-derivation — resolving it further is
out of this small, ergonomics-only item's scope.

**Review and report only.** Same guarantee as `/review-implementation`:
never writes `docs/ACTIVE_MILESTONE.md`, `<feedback_dir>/FUNCTIONAL_REVIEW.md`,
or `docs/ai-workflow/WORKFLOW_STATE.json`; never fixes findings; never
transitions `phase`. `/apply-functional-review` remains the sole
authoritative remediation path, and `/accept-milestone`/
`/accept-scoped-remediation` remain the sole, user-only acceptance gates,
all entirely unchanged.

Steps:

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`). **Single untracked-item policy
   (revision 10, round 9 I1 fix — resolves `GPT-R9-001`)**: refuse cleanly,
   naming the problem, if neither resolves to an existing
   `docs/ai-workflow/WORKFLOW_STATE.json` `work_items` entry — this command
   needs a live `phase` to guard against, mirroring `/review-implementation`
   step 1 exactly; an untracked item has no `phase` for this command to
   check and is out of scope for it. No separate no-phase branch is
   specified or wanted for this small release; see "Revision 10" below for
   why this differs from revision 9's own text here.
2. **Phase guard**: if the resolved item's `phase` is not exactly
   `AWAITING_FUNCTIONAL_REVIEW`, refuse cleanly, naming the actual phase.
   Version-independent, same reasoning as `/review-implementation`.
3. **Locate the checklist's current evidence**: read `implementation_revision`
   live from the work item; call
   `workflow_state.discover_current_functional_checklist_evidence(repo_root,
   work_item_id, base_commit, head=<current HEAD>,
   implementation_revision=<that value>)`. No result: refuse, naming the
   round — no checklist evidence is committed to review yet (report that
   `/prepare-functional-review` has not completed its checklist-evidence
   provenance commit for this round). A result: compare its `"blob"` against
   `git hash-object docs/ACTIVE_MILESTONE.md`'s live working-tree content —
   a mismatch means the checklist has been edited since its own evidence
   commit. **Refuse rather than review stale evidence (revision 10, round 9
   O1 — resolves `GPT-R9-O01`)**: refuse cleanly, naming both blobs
   prominently, and tell the user to rerun `/prepare-functional-review` so
   this review is anchored to committed checklist evidence — never silently
   review against the live working-tree text instead (revision 9's own text
   here did the opposite: report the mismatch, then continue reviewing the
   live text anyway; dropped, since a refusal removes any ambiguity about
   exactly which checklist the advisory opinion covers, at no cost — the
   user re-running `/prepare-functional-review` is already the required
   step before any real functional-review round can proceed). **Third
   outcome (revision 9, round 8 I1)**: the callee also raises
   `NonFirstParentFunctionalChecklistEvidenceError` when round-scoped
   evidence commits exist in `base_commit..head` but none sits on `head`'s
   first-parent chain (e.g. evidence prepared on a side branch merged
   without ever becoming a first-parent transition) — report this as a
   clean refusal naming that cause, never an uncaught traceback. Inherited
   from `/prepare-functional-review` step 3a, which calls the same function
   and is equally silent about this exception — not a new gap introduced
   here.
4. **Read**: the checklist section itself (the current round's section in
   `docs/ACTIVE_MILESTONE.md`), the diff since `base_commit` (or since the
   prior round's own checklist-evidence commit, for a continued round) via
   `git diff`/`git log`, and `technical_approval` — must be `status:
   CURRENT`; if `STALE`, report this prominently, since the checklist may
   predate a since-invalidated implementation.
5. **Independently verify**:
   - re-run every automated-verification command the checklist cites
     yourself, and confirm the actual output matches what the checklist
     claims (never trust the claim at face value);
   - assess the checklist's manual-flow coverage against the actual diff —
     does every behaviorally-significant change since the relevant base
     have at least one flow that would exercise it; name any gap;
   - flag any claimed-passing check you could not reproduce, any missing
     setup/test-data step, and any ambiguous or untestable "expected
     result" wording.
6. **`REJECTED`-marker refusal, this command's sole assertion, immediately
   preceding the report**: call `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here, before composing the report. The marker is
   work-item-scoped, not bundle-scoped — a withdrawn work item must not
   receive an advisory functional-review opinion, matching `/apply-
   functional-review`'s own bounded-fix branch, which is a required
   consumer of this same assertion for the same reason. **Diagnosability
   note (revision 3, round 2 O2)**: `resolve_rejected_marker_path` resolves
   to the flat, work-item-agnostic `.ai-review/REJECTED` whenever no scoped
   `.ai-review/<work_item_id>/current/` directory exists yet — an inherited
   hazard shared with every other consumer of this assertion, not new here
   — so if this refuses unexpectedly, report both the scoped and flat
   candidate marker paths rather than only the one actually found.
7. **Compose the report**: a checklist-completeness/evidence-reproducibility
   opinion — not a bundle-style `Status: APPROVE | REVISE | BLOCK` verdict,
   since there is no bundle or ledger stage to gate here (state this
   explicitly, so the report is never mistaken for a `REVIEW_FEEDBACK.md`-shaped
   verdict) — covering: which automated checks reproduced cleanly and which
   did not; which behavioral changes the checklist covers and which it does
   not; concrete gaps the user should address in the checklist before
   spending time on the manual pass; and an explicit, prominent reminder
   that only the user's own manual execution of the checklist's required
   functional flows can actually satisfy this gate — an app walkthrough for
   product/UI work items, command/process checks for process work items
   (**work-item-neutral wording, revision 10, round 9 I2 fix — resolves
   `GPT-R9-002`**, same correction as the justification paragraph above) —
   this report is a pre-check, not a substitute for it.
8. **Report only — write nothing.** Print the report directly in this
   turn's response. Nothing at `docs/ACTIVE_MILESTONE.md`,
   `<feedback_dir>/FUNCTIONAL_REVIEW.md`, `docs/ai-workflow/WORKFLOW_STATE.json`,
   or any other repository file is touched. Never approve, never fix
   findings, never transition `phase`, never auto-continue to any other
   command — `/apply-functional-review` is the only command that ever acts
   on a real `FUNCTIONAL_REVIEW.md`, and only the user can invoke
   `/accept-milestone`/`/accept-scoped-remediation`.

Do not implement product or test code in this command. Do not edit
`docs/ACTIVE_MILESTONE.md`, the plan/registry/mapping files, or any other
command file.

### Shared wiring (both commands)

- **Prerequisite, CP1's own new first step (revision 5, round 4
  B1/B2/I1/I2/missing tests, superseding revision 4's version of this same
  step — must land before the command file is created, since the
  declaration below is its own `implementation_stage.protected_paths`
  entry and every item below is what CP1's own verification step depends
  on passing)**:
  - Widen `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s
    `implementation_stage` per "Revision 3" (B1) above's exact
    protected/excluded split, **plus** two additional exact
    `excluded_paths` entries per "Revision 4" (B2) above:
    `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` and
    `docs/ACTIVE_MILESTONE.md` — **plus** one additional exact
    `protected_paths` entry per "Revision 4" (O2) above:
    `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json` (the file
    the very next bullet below edits), so this item's own edit to it enters
    this item's own implementation-stage `review_content_manifest` instead
    of resolving `excluded` under the new `docs/ai-workflow/registry/`
    prefix — **plus** the full concurrency-hardening set both sibling
    declarations already carry, per "Revision 5" (I2) above: `excluded_paths`
    gains `.gitignore`, `AGENTS.md`, `README.md`, `docs/PROJECT_BRIEF.md`,
    `docs/DOMAIN_GLOSSARY.md`, `docs/UX_FLOWS.md`, `docs/ROADMAP.md`,
    `docs/ai-workflow/WORKFLOW_CONFIG.json`,
    `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`,
    `docs/ai-workflow/WORKFLOW_V2_PLAN.md`,
    `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`, `docs/TECHNICAL_DECISIONS.md`
    (rationale mirrored verbatim from `workflow-v2-1-core-artifacts.json`'s
    own identical entries); `excluded_prefixes` gains
    `docs/ai-workflow/archive/`, `docs/milestones/`, `docs/adr/`,
    `docs/agent-context/`, `.github/`, `docs/ai-workflow/dry-run/`,
    `docs/improvements/`, `app/`, `config/`, `gradle/` (same mirrored
    rationale) — so a concurrent write anywhere in the repository outside
    this item's own declared footprint excludes cleanly instead of raising
    `UnclassifiedPathError`, matching the defensive breadth
    `workflow-v2-1-core-artifacts.json`'s own `implementation_stage` already
    commits to, and — **corrected, revision 9, round 8 B1**: not, as
    previously claimed here, breadth this item's own `plan_stage` section
    already committed to independently; measured directly, it did not,
    until "Revision 9" (B1) below added the three missing entries
    (`docs/TECHNICAL_DECISIONS.md`, `docs/ai-workflow/WORKFLOW_V2_PLAN.md`,
    `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`) as this item's own
    `plan_stage.excluded_paths`. As of that revision the two sections do
    match, but as a consequence of that fix landing, not as a pre-existing
    fact this bullet could correctly assume. **Already present, revision 13
    round 11 O2**: this file's live `implementation_stage` also already
    carries one further `excluded_paths` entry
    (`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`) and one further
    `excluded_prefixes` entry (`docs/ai-workflow/diagrams/`), written
    directly by "Revision 11" and "Revision 12" (I1) respectively, outside
    this bullet's own enumeration — both are carried forward as part of
    this widening's target state, not additions this bullet itself makes.
  - Widen `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`'s
    `implementation_stage` — **three additions, revision 12, round 10 B1**
    (superseding revision 6's single-entry version of this bullet, which
    predates the two files "Revision 11" discovered): `excluded_paths`
    gains `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` (per "Revision 3"
    (B2)/"Revision 4" (B2) above) and
    `docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`; `excluded_prefixes`
    gains `docs/ai-workflow/diagrams/` — kept for
    `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`,
    the one real-repository test that cannot be re-anchored to a fixed
    commit (below), whose dirty-set scope changed once the two "Revision
    11" files appeared in the working tree: `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`
    is this item's own `plan_path` and leaves the dirty set once
    `resolve_plan_stage_approval_commit_paths` commits it at plan approval
    (`D-Approval-Commits`) — so, by itself, that one entry no longer serves
    the purpose it is justified by, by the time CP1's own gate runs — but
    the two "Revision 11" files are concurrent, untracked operator-reference
    content this item's own commits never touch, so they remain dirty
    through and past CP1 until some other, later commit adds them; both
    must classify for the gate to pass. **This is the one and only edit
    this item makes to `workflow-v2-1-core-artifacts.json`** (revision 6,
    round 5 O1, now covering the file as a whole rather than a single
    entry) — every other widening in this bullet targets this item's own
    `workflow-v2-3-artifacts.json` instead.
  - Add the new `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT =
    "27f051eba897d77c742ead8b160ed519c0671ee4"` constant to
    `scripts/workflow_fingerprint_demo_test.py` and
    `scripts/workflow_state_demo_test.py`, and re-anchor **seven**
    real-repository tests to it (fixed-commit scope, not `"HEAD"`/worktree),
    per "Revision 4" (B1) above's exact function substitutions, "Revision 5"
    (B1) above's sixth addition, and "Revision 6" (B1) above's seventh:
    `test_demonstration_against_real_repo`,
    `test_142_generalized_resolver_reproduces_the_migrated_digest_not_a_hardcoded_literal`
    (**both calls take the swap (revision 8, round 7 I1)**: the generalized-
    resolver digest at `:124` swaps to
    `compute_review_content_id_plan_stage_at_commit_for_work_item(repo_root,
    "workflow-v2-1-core", WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
    base=BASE_COMMIT)` as already stated, **and** the frozen-defaults
    comparison digest at `:128` swaps from the worktree-source
    `compute_review_content_id_plan_stage` to
    `compute_review_content_id_plan_stage_at_commit(repo_root, BASE_COMMIT,
    WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, work_item_type="process",
    work_item_id="workflow-v2-1-core", plan_revision=plan_revision,
    protected=wf.PLAN_STAGE_PROTECTED,
    excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
    excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES)`, with `plan_revision`
    itself re-sourced at `:127` from
    `wf.load_plan_revision(repo_root, wf.DEFAULT_REGISTRY_PATH,
    wf.DEFAULT_PLAN_PATH, at_commit=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)`
    rather than the current live-worktree read (no `at_commit`) — swapping
    only `:124` would leave `digest_generalized` commit-anchored while
    `digest_frozen_defaults` (and the `plan_revision` feeding it) stayed
    worktree-source, the identical cross-source `assertEqual` this document's
    own `_blob_at_commit` repair (below) and the item-4 fix later in this
    same bullet each spent a round closing one test away from this one),
    `test_real_diff_since_base_commit_classifies_exhaustively`, and
    `test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas`
    (all in `workflow_fingerprint_demo_test.py` — **both calls in the
    latter test take the swap (revision 7, round 6 I1)**: the manifest
    computation (`:427`) and the `digest2` idempotency recompute (`:464`,
    feeding `assertEqual(digest, digest2)` at `:470`) both currently call
    `compute_review_content_id_implementation_stage` and both move to
    `compute_review_content_id_implementation_stage_at_commit(repo_root,
    BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, ...)` — swapping only
    the first would anchor `digest` at
    `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` while `digest2` stayed
    worktree-source, reintroducing at CP1's own commit the identical
    cross-source comparison the `_blob_at_commit` repair below just fixes
    one loop above it), plus
    `test_real_plan_approval_is_current`,
    `test_real_implementing_entry_is_reachable_at_current_head`, and
    `test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`
    (all three in `workflow_state_demo_test.py`, each via an explicit
    `head=` argument —
    `ws.approval_is_current(..., head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)`,
    `ws.implementing_entry_reachable(..., head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)`,
    and the same treatment for the third, at `:857`, respectively — the
    third lives in `TestCheckpointOriginationAgainstRealRepository`, a
    different class from the first two's `TestAgainstRealRepository`, which
    is why three consecutive `TestAgainstRealRepository`-scoped
    measurements missed it before revision 6). No frozen `PLAN_STAGE_*`
    constant and no byte of either artifacts-declaration file's
    `plan_stage` section changes as part of this step.
    **Bound-evidence disclosure (revision 6, round 5 B1/I1)**: two of these
    seven tests are `workflow-v2-1-core`'s own recorded completion-ledger
    evidence, re-executed by
    `TestReconciliationTableLedgerStatusAgreement` under a real pinned
    worktree at live `HEAD` — `test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`
    is item 352's bound evidence and
    `test_real_diff_since_base_commit_classifies_exhaustively` is item
    359's (`docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json`).
    Omitting the third call site above from this substitution would leave
    item 352's bound evidence failing under that pinned re-execution at
    every subsequent `HEAD`, permanently — the fix above clears it.
    Re-anchoring item 359's own test converts its bound evidence from an
    open-ended, live-working-tree assertion ("every path changed since
    `base_commit` classifies") to a fixed-history one (`workflow-v2-1-core`'s
    own `162154d3..27f051eb` diff, permanently, unaffected by any later
    commit); live, open-ended changed-set coverage for whichever work item
    is *currently* active moves instead to the new active-work-item-scoped
    test below (missing tests, item 1).
  - **Cross-source comparison repair (revision 5, round 4 B2/I1)**: add a
    small test-local helper, `_blob_at_commit(repo_root, commit, rel_path)`
    (`git rev-parse f"{commit}:{rel_path}"`), to both `_demo_test.py`
    files, and use it in place of the live-worktree
    `real_sha = wf._hash_object(repo_root, entry["path"])` comparison in
    both `test_demonstration_against_real_repo`'s and
    `test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas`'s
    own manifest-verification loops — `real_sha = _blob_at_commit(repo_root,
    WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, entry["path"])` — so the manifest
    entry (fixed at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` by the substitution
    above) and its verification are compared from the same fixed source,
    never worktree-vs-commit, regardless of what CP1 or any later checkpoint
    edits in the working tree.
  - Add `27f051eba897d77c742ead8b160ed519c0671ee4` to
    `scripts/workflow_state_demo_test.py`'s
    `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`, per "Revision 3"
    (I2) above.
  - Add three new `workflow_state_demo_test.py` real-repository tests
    (revision 9, round 8 missing tests, widened from the two "Revision 4"
    (missing tests) originally scoped — below): one that runs
    `load_implementation_stage_classification`/`classify_path_implementation_stage`
    over `_changed_tracked_paths_between(repo_root, work_item["base_commit"],
    "HEAD")` against `artifacts_path_for_work_item(active_work_item_id)`
    (changed-set, not dirty-set, so it actually observes a committed path
    like B2's own two — **stated purpose note (revision 7, round 6
    Migration and data-integrity concerns, non-numbered)**: this test
    deliberately reads live `HEAD` scoped to
    `active_work_item_id`'s own artifacts file, so a future work item with
    an under-declared implementation-stage classification turns this test
    red — that redness is a signal about whichever item is *currently*
    active, never about `workflow-v2-3` itself once this item completes and
    a later item becomes active); one that asserts
    `assert_all_changed_paths_classified_commit(repo_root, BASE_COMMIT,
    WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, protected=wf.PLAN_STAGE_PROTECTED,
    excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
    excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES)` raises nothing,
    naming B1's own failure class directly; **and a third, the plan-stage
    counterpart of the first (revision 9, round 8 missing tests — B1 above
    is precisely the defect class this new test would have caught, one
    stage over, and the first two do not: the first is implementation-stage
    only, the second is pinned to `workflow-v2-1-core`'s own frozen
    constants and a closed history range, so neither exercises the active
    item's own live plan-stage declaration)**: resolve
    `wf.resolve_plan_stage_metadata(repo_root, active_work_item_id)` and
    assert every path in `_changed_tracked_paths_between(repo_root,
    work_item["base_commit"], "HEAD")` classifies via `wf.classify_path`
    without raising `UnclassifiedPathError` — same shape and same
    live-`HEAD`-scoped stated-purpose note as the first test above, applied
    one stage over. This deliberately live `"HEAD"` call is on
    `_changed_tracked_paths_between`, a function outside the five-function
    scanned set the static-conformance test below covers, so it needs no
    allowlist entry there either, for the same reason given below.
  - Add two more new tests, per "Revision 5" (missing tests) above: a
    static-conformance test asserting every `_demo_test.py` call site into
    `approval_is_current`/`implementing_entry_reachable`/
    `compute_review_content_id_*_at_commit*` never resolves its `head`/
    `commit` argument to live `"HEAD"` or to the parameter's own default —
    every one passes a fixed commit constant
    (`WORKFLOW_V2_1_CORE_COMPLETION_COMMIT`, `REVIEWED_CONTENT_COMMIT`,
    `WF_M8A_COMMIT`, or the test's own frozen literal), by keyword **or
    position** (revision 7, round 6 B1 — corrected from "an explicit
    `head=`/`commit=` keyword argument," which both over- and under-fires:
    it would let a call written `head="HEAD"` explicitly — the exact defect
    class — pass, and it wrongly flags
    `workflow_state_demo_test.py:634`
    (`TestLegacyImportAgainstRealMilestone8.test_backfilled_review_content_id_reproduces_from_declarations_as_originally_authored`),
    which is already correct — pinned to the frozen literal
    `REVIEWED_CONTENT_COMMIT`, reads no live `HEAD` — but passes `commit`
    positionally, third argument, legal against the real signature).
    **Anchor resolution rule (revision 8, round 7 B1 — a property alone does
    not say how the scan finds the argument it inspects)**: "by keyword or
    position" is not a uniform rule across the five scanned functions —
    `commit` sits at positional index 2 for the three `_at_commit*`
    functions, but `implementing_entry_reachable`'s anchor is `head` at
    index **3** (`repo_root, work_item, base_commit, head="HEAD"`,
    `scripts/workflow_state.py:1574-1576`), and `approval_is_current`'s is
    `head`, keyword-only (`scripts/workflow_state.py:1545-1547`) — so a scan
    written from "the anchor is the third argument," the reading this
    document's own B1 text (above and at "Revision 4" (B1)) states twice for
    the `_at_commit*` functions, silently reads `implementing_entry_reachable`'s
    `base_commit` instead and passes both of its real omitted-anchor call
    sites (`:565`, `:857`) as already-conforming. The scan therefore resolves
    the anchor by importing each scanned callable
    (`workflow_state.approval_is_current`,
    `workflow_state.implementing_entry_reachable`,
    `workflow_fingerprint.compute_review_content_id_plan_stage_at_commit`,
    `..._at_commit_for_work_item`,
    `compute_review_content_id_implementation_stage_at_commit`) and binding
    each call's parsed arguments against the real signature with
    `inspect.signature(fn).bind_partial(*args, **kwargs)` — the bound
    parameter name is `head` for the first two functions, `commit` for the
    remaining three. A call whose bound arguments omit that parameter
    entirely is reading the parameter's own live default and flags; a call
    whose bound value for that parameter is the literal string `"HEAD"` also
    flags; every other bound value passes. No hand-maintained positional-
    index map is stated or needed — the index a function's anchor sits at is
    derived from `inspect.signature` itself at scan time, not restated by
    this document, so it cannot independently drift from the real signatures
    the way this bullet's own property statement already has, across four
    consecutive rounds. **`:634` is a real call site into these five
    functions, already conforming and left unchanged by this checkpoint, and
    it is not the only one this document's own tallies have undercounted
    (revision 8, round 7 I2 — this bullet's two call-site totals previously
    disagreed, "eight" here against "fifth through seventh" below; both are
    corrected together, derived once)**: today, before any CP1 substitution
    lands, exactly four real call sites into the five scanned functions exist
    (`:565`, `:572`, `:634`, `:857`, measured below). Items 1, 2, and 4 each
    swap a call from a worktree-source sibling to one of the five scanned
    names and each contributes new scanned call sites once applied — item 1
    one (`test_demonstration_against_real_repo`), item 2 two (`:124` and
    `:128`, revision 8 round 7 I1, above), item 4 two (`:427` and `:464`,
    revision 7 round 6 I1) — five new sites in total; items 5-7 fix the
    anchor at three of today's already-scanned four (`:572`, `:565`, `:857`)
    rather than adding new ones; item 3 swaps `_changed_tracked_paths_between`,
    a function outside the scanned set entirely, and never becomes one, by
    design. The scan's true post-CP1 scope is **nine** call sites — today's
    four, plus the five items 1/2/4 add — not seven and not eight; the
    enumeration is honest only once `:634` and both of item 2's calls are
    named alongside the rest. **No allowlist entry (revision 7, round 6 B1, dropping
    the previously named one)**: the property, scoped to exactly these
    three named patterns spanning five function names (revision 9, round 8
    I2 — this descriptor was left at its pre-revision-8 "three" value here
    while the count fix landed correctly elsewhere in this same bullet;
    both now say five, one vocabulary throughout), never reaches
    `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`'s
    own `load_implementation_stage_classification`/`any_protected_path_dirty`
    call (neither name matches) or either of the two new
    active-work-item-scoped tests' (implementation-stage and, revision 9,
    round 8 missing tests, its plan-stage counterpart) own deliberately
    live `_changed_tracked_paths_between(repo_root, work_item["base_commit"],
    "HEAD")` calls (a different function entirely) — so none of the three
    needs an exemption, and the previously named
    allowlist entry exempted a call the scan could never have flagged in
    the first place (confirmed by reading it directly: it calls neither
    scanned function). **Negative control, per function rather than per
    shape (revision 8, round 7 B1/missing tests — supersedes revision 7's
    version of this same control, which pinned two fixture *shapes* but no
    per-function *coverage*, so a scan that misreads any one function's
    anchor slot could still pass it whenever the omitted-anchor fixture
    happened to be written against a different, correctly-resolved
    function — exactly what B1's mis-indexed-scan measurement above
    shows)**: the test asserts the scan flags one synthetic anchor-omitted
    fixture call written against **each** of the five scanned function names
    (`approval_is_current`, `implementing_entry_reachable`,
    `compute_review_content_id_plan_stage_at_commit`,
    `compute_review_content_id_plan_stage_at_commit_for_work_item`,
    `compute_review_content_id_implementation_stage_at_commit` — five short
    fixture lines, not one), plus one fixture call with an explicit
    `head="HEAD"`, and that its observed real-call-site count is non-zero,
    so a silently-broken callee-matcher (wrong attribute/name resolution, a
    missed `ws.`/`fingerprint.` prefix, a module-alias change) cannot pass
    vacuously by finding nothing, and a scan that resolves any single
    function's anchor slot incorrectly fails this control rather than
    passing it. **Gate is the test
    passing, not a hand-checked list (revision 6, round 5 missing tests)**:
    this test must scan both `_demo_test.py` files for every real call site
    into the named functions and fail on any that resolves its anchor to
    live `"HEAD"`/the default — never a hand-enumerated tally of "the known
    N offenders," since hand enumeration of this exact call set has come up
    short three consecutive rounds running (five → six → seven), and a
    fourth time at the property-statement level itself this round; the
    test's whole value is not depending on this document's own
    enumeration, or its own stated property, being complete or correct.
    **Measured (revision 7, round 6 B1's own acceptance criterion 2 — a
    specification is not a measurement)**: the corrected scan, prototyped
    against both `_demo_test.py` files as they stand at this plan revision
    (before CP1's own seven substitutions land), finds exactly four real
    call sites into the five scanned functions (revision 9, round 8 I2:
    corrected from "three named functions", stale here since revision 8
    fixed the count but not this descriptor): `workflow_state_demo_test.py:634`
    (`OK`, fixed constant, positional), and `:565`/`:572`/`:857` (`FLAG`,
    omitted → live default `"HEAD"`) — the three of the seven substitutions
    that are today already calls into one of these exact function names.
    The other four substitutions (items 1/2/4, which swap toward
    `compute_review_content_id_plan_stage_at_commit`/
    `..._for_work_item`/`compute_review_content_id_implementation_stage_at_commit`;
    and item 3, which swaps `_changed_tracked_paths_between`, a function
    outside the scanned set entirely) do not exist yet in scanned-function
    form at this plan revision, so they are correctly invisible to this
    measurement; items 1/2/4 become the fifth through ninth scannable call
    sites (all expected `OK`, fixed at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT`)
    once CP1's own substitutions land — item 1 the fifth; item 2 the sixth
    and seventh (`:124` and `:128`, both calls, revision 8 round 7 I1); item
    4 the eighth and ninth (`:427` and `:464`, both calls, revision 7 round 6
    I1) — and item 3 never becomes one, by design. Full scan output pasted
    in `TEST_RESULTS.md`. And a hermetic
    regression test in `scripts/workflow_fingerprint_test.py`,
    using the existing `ScratchRepo` fixture, that commits a protected file
    at commit A, edits its working-tree content without committing, and
    asserts a commit-anchored manifest recompute at commit A still reports
    the commit-A blob rather than the dirty worktree content — pinning the
    class of defect B2/I1's `_blob_at_commit` repair fixes, not only this
    repository's own instance of it.
  - Run `workflow_fingerprint_demo_test.py`/`workflow_state_demo_test.py`
    (including `TestReconciliationTableLedgerStatusAgreement`, the one
    class that observes a change to bound ledger evidence B1/I1 above touch
    — named explicitly here per revision 6, round 5 missing tests) to
    confirm all seven originally-failing real-repository tests now pass and
    the one pre-existing (I2) failure is grandfathered, before proceeding
    to the rest of CP1.
- **Forced update** (revision 2: `EXPECTED_CALL_SITES` is the only one of
  the three registrations below that a hermetic suite fails closed on if
  skipped — `TestAssertLocalGenerationMatchesCallSiteConformance.test_exactly_the_two_live_permissive_callers_exist`
  glob-scans every `.claude/commands/*.md` file, plus every `scripts/*.py`
  file except `workflow_fingerprint.py` itself and any `*_test.py` module
  (**revision 10, round 9 O4 correction, resolves `OPUS-R9-O02`**: revision
  2's own text here named only the command-file scan, omitting the
  production `scripts/*.py` call-site scan the test also performs — the
  required implementation action is unchanged, this is a description-only
  correction), and asserts the found set
  equals `EXPECTED_CALL_SITES` exactly): update
  `scripts/workflow_integration_test.py::TestAssertLocalGenerationMatchesCallSiteConformance`
  to add `.claude/commands/review-implementation.md` to `EXPECTED_CALL_SITES`
  (not `review-functional.md`, which never calls that function), rename
  `test_exactly_the_two_live_permissive_callers_exist` to name three, and
  update its class docstring's "exactly two live call sites" claim to three.
- **Deliberate, merits-based decisions** (revision 2: `discover_review_subject_declarations`
  iterates `REVIEW_SUBJECT_ROSTER` only and `TestGoldenCommandFileHashes`
  iterates its own dict only — neither fails if a new file is left off, so
  both additions below are design decisions, not mechanical necessities;
  see the revision 2 disposition above for why both are still made, unlike
  `.claude/commands/recover-implementation-provenance.md`'s deliberate,
  acknowledged non-registration):
  - Add both new paths to `scripts/workflow_state.py`'s
    `REVIEW_SUBJECT_ROSTER` (`.claude/commands/review-implementation.md`,
    `.claude/commands/review-functional.md`).
  - Update `scripts/workflow_state_demo_test.py::TestReviewSubjectDeclarationsLive`:
    add both new entries to `EXPECTED` (`review-implementation.md: "bundle"`,
    `review-functional.md: "bundle"` — see the "Design" section's `/review-
    functional` classification above, revised from `"none"`), add
    `review-implementation.md: 1` and `review-functional.md: 1` to
    `EXPECTED_ASSERTION_COUNT` (report-only single-assertion pattern,
    matching `prepare-review.md`/`milestone-implement.md`/`milestone-plan.md`/
    `apply-functional-review.md`). `len(exempt)` **stays 4** — neither new
    file is `"none"` — so `test_every_exempt_file_never_calls_the_assertion`
    needs no change beyond the roster now finding 15 total declared files.
    Rename the "all thirteen"-named test method/docstring to track the real
    count as each command lands (fourteen after CP1, fifteen after CP2).
    **Also update the class docstring's "nine-consumer/four-exempt split"
    phrase (revision 6, round 5 O2)**: both new files are `bundle`
    consumers, so once both land the real split is eleven-consumer/
    four-exempt, not nine — cosmetic (no test asserts this exact prose,
    `len(exempt) == 4` is the only live assertion and it stays 4), but in
    the same sentence the rename above already touches.
  - Add an entry to `scripts/workflow_integration_test.py::TestGoldenCommandFileHashes`'s
    `_GOLDEN_COMMAND_FILE_SHA256` for each new file, computed once its
    content is finalized.
- Add a new static-conformance test class in `scripts/workflow_integration_test.py`
  (mirroring `TestBootstrapCommandStaticConformance`'s/
  `TestVersion21OnlyCommandsRefuseCleanlyForV1`'s pattern of asserting key
  invariant sentences are actually present in the file's real text) covering,
  for each new command: the report-only constraint ("never modify... never
  approve... never advance"), the model-independence framing, the phase
  guard naming the exact required phase, the explicit "writes nothing"
  statement, and both files' `description:`/`argument-hint:` frontmatter
  (every existing command file carries both; revision 1 left them
  unspecified). Also assert (revision 2, closing a missing-test gap):
  `review-implementation.md`'s text names `artifacts_path_for_work_item`
  (so a regression back to `load_implementation_stage_classification`'s
  default argument fails this cheap textual check, per I3 above). **Also
  assert (revision 7, round 6 missing tests)**: `review-implementation.md`'s
  text names `MissingRequiredBundleFileError` (the absent-bundle refusal
  B2/revision 6 I2's step 3/4 text is built around), the same cheap
  textual-invariant style already applied to `artifacts_path_for_work_item`
  above — so a regression that silently drops the missing-manifest refusal
  wording out of the command file fails this check too. **Revision
  3 correction (round 2, missing tests)**: do **not** also assert
  `review-functional.md` contains `assert_bundle_not_rejected` exactly
  once — that duplicates
  `EXPECTED_ASSERTION_COUNT`'s new `review-functional.md: 1` entry above
  (`workflow_state_demo_test.py::test_every_non_exempt_file_calls_the_shared_assertion_the_expected_number_of_times`
  already counts the same token in the same file). If a second static
  assertion for `review-functional.md` is wanted, use O1's two previously
  uncovered constraints instead: it contains no
  `assert_local_generation_matches(` substring, and exactly one distinct
  `review-subject:` value.
- Update four documentation sites (revision 2, I2 above) to reflect
  `/review-implementation` as a third live `assert_local_generation_matches`
  caller, alongside `/approve-review`/`/review-plan`:
  1. `scripts/workflow_fingerprint.py`'s `assert_local_generation_matches`
     docstring ("**Repository-local commands only**" list and "both callers
     above use it, unchanged").
  2. `scripts/workflow_fingerprint.py`'s `WorktreeOrHeadMismatchError`
     docstring ("Raised by a **repository-local** consumer" list).
  3. `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Generation diagnostic
     metadata" section ("today `/approve-review` and `/review-plan`").
  4. `scripts/workflow_integration_test.py::TestAssertLocalGenerationMatchesCallSiteConformance`'s
     class docstring and `TestGenerationDiagnosticMetadataCallerWordingConformance`'s
     two tests (`test_review_protocol_names_both_live_callers`,
     `test_worktree_or_head_mismatch_docstring_is_non_exclusive`,
     `test_assert_local_generation_matches_docstring_is_non_exclusive`) —
     these use `assertIn`, so they do not fail on an unmentioned third
     caller, but they are updated anyway to actually name it, since that is
     exactly the "active caller-facing documentation must agree with...
     reality" standard their own class docstring states.
- Update `scripts/workflow_fingerprint.py`'s `assert_bundle_not_rejected`
  docstring to add both `/review-implementation` and `/review-functional`
  to its "every required consumer" enumeration (documentation only — no
  test asserts this docstring's exact text, confirmed by grep before
  writing this plan; done for consistency with how this repository already
  treats every other consumer-enumeration site).
- `docs/ai-workflow/MILESTONE_WORKFLOW.md`: add one bullet to
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`'s "Allowed actions" noting
  `/review-implementation` as an optional, non-gating action, and one
  equivalent bullet to `AWAITING_FUNCTIONAL_REVIEW`'s "Allowed actions" for
  `/review-functional`. Add one clarifying sentence to "Hard gates summary"
  noting both are optional actions inside existing gates, adding no new
  gate (the count stays exactly 6) — mirroring how that section already
  clarifies the plan stage's two-stage refinement is not two additional
  gates.
- `docs/ai-workflow/REVIEW_PROTOCOL.md`: add a short "Local reviewer
  commands (operator ergonomics)" section describing what both commands do,
  their report-only nature, how a user may hand-copy a report into
  `<feedback_dir>/REVIEW_FEEDBACK.md` themselves if they choose to treat it
  as the authoritative external round (never automatic), and one sentence
  noting the two commands' reports are deliberately differently shaped
  (`/review-implementation`'s is `Status: APPROVE | REVISE | BLOCK`-shaped;
  `/review-functional`'s is a checklist-completeness opinion, never that
  shape) so the difference reads as intentional rather than an
  inconsistency between an otherwise-matched pair. This section may also
  contrast `/review-functional`'s `review-subject: bundle` against
  `/prepare-functional-review`'s `none` (per the "Design" section's I1
  reconciliation above) — describe that contrast in prose only, as a house-style
  choice (prose reads better than a fenced declaration line here, and it keeps
  the file future-proof if `REVIEW_SUBJECT_ROSTER` ever widens to include
  `REVIEW_PROTOCOL.md` itself), **not** as a mechanical constraint (revision 9
  correction, round 8 O2: the previously stated rationale was wrong —
  `_parse_review_subject_declarations` (`scripts/workflow_state.py:6799-6803`)
  has exactly one caller, `discover_review_subject_declarations`, which
  iterates `REVIEW_SUBJECT_ROSTER` only — thirteen `.claude/commands/*.md`
  paths, never `REVIEW_PROTOCOL.md` — so no fenced literal here can actually
  fail anything; stating it as a mechanical constraint risked a later reader
  generalizing a scanner that does not scan this file). The distinct,
  correctly-grounded constraint nearby — never write a call-shaped
  `assert_local_generation_matches(` substring inside `/review-functional`'s
  own file — is unaffected by this correction and remains stated in the
  "Design" section above, where `TestAssertLocalGenerationMatchesCallSiteConformance`'s
  real glob-scan actually applies.
- `CLAUDE.md`: bring the "Slash commands" list in the "Commands and
  detailed workflow docs" section fully current — add the five existing
  command names it currently omits (`bootstrap-workflow-v2`,
  `record-manual-plan-review`, `review-plan`, `accept-scoped-remediation`,
  `recover-implementation-provenance`) alongside this plan's two new ones,
  rather than silently extend an already-incomplete, unstated subset
  (revision 2, I5 above).

## Requirements traceability

| Requirement | Description | Checkpoints |
|---|---|---|
| REQ-1 | Add a model-independent `/review-implementation <work-item-id>` command, analogous in philosophy to `/review-plan`. | CP1 |
| REQ-2 | Evaluate, and (found justified) add, a model-independent `/review-functional <work-item-id>` command for reviewer-side symmetry. | CP2 |
| REQ-3 | Review commands must work with any capable model; never hard-code Opus/Sonnet/GPT/another provider or model. | CP1, CP2 |
| REQ-4 | Do not add `AWAITING_LOCAL_*`, `AWAITING_INTERNAL_*`, `AWAITING_MODEL_*`, or equivalent new lifecycle states in this release. | CP1, CP2 |
| REQ-5 | Reuse existing review states only: `/review-implementation` operates while `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`; `/review-functional` while `AWAITING_FUNCTIONAL_REVIEW`. | CP1, CP2 |
| REQ-6 | Reviewer commands review and report only: never modify implementation/source/test content, approve the stage, apply findings, or advance workflow state. | CP1, CP2 |
| REQ-7 | Preserve the existing apply-plan-review/apply-implementation-review/apply-functional-review commands, unmodified, as the sole authoritative remediation paths. | CP1, CP2 |
| REQ-8 | Keep the change small and ergonomic; defer richer local-vs-external reviewer orchestration and controller-aware lifecycle states to Workflow v3. | CP1, CP2 |

## Checkpoint registry (generated; do not hand-edit — regenerated at each
## plan revision from `scripts/workflow_state.py`'s `generate_registry`)

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Add /review-implementation command | - | 2 | 1 |
| CP2 | Add /review-functional command, shared docs, final verification | CP1 | 2 | 1 |

**CP1** — **first, before any command file is created** (revision 8, round 7
B1/I1/I2/O1/missing tests further correct the static-conformance test's own
anchor-resolution rule, negative control, and call-site count, the item-2
substitution below, and `/review-implementation` step 4's diagnosability,
superseding revision 7's version of this paragraph, which itself superseded
revision 6's, which itself superseded revision 5's — revision 6, round 5
B1/I1/I2/O1/O2/missing tests remain the paragraph's base): widen `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s
`implementation_stage` (including the two additional exact `excluded_paths`
entries, `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` and
`docs/ACTIVE_MILESTONE.md`, revision 4 B2; one additional exact
`protected_paths` entry, `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`,
revision 4 O2; the full concurrency-hardening set both sibling
declarations already carry, twelve `excluded_paths` and ten
`excluded_prefixes` entries, revision 5 I2; and — already present and
carried forward, not this widening's own addition — the one `excluded_paths`
entry (`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`) and one
`excluded_prefixes` entry (`docs/ai-workflow/diagrams/`) revisions 11 and 12
already wrote into this same file, revision 13 round 11 O2) and
`docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`'s
`implementation_stage` (this item's one and only edit to that file,
revision 6 O1, now three additions per revision 12 round 10 B1: two
`excluded_paths` entries — `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md` and
`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` — and one
`excluded_prefixes` entry, `docs/ai-workflow/diagrams/`); add the
`WORKFLOW_V2_1_CORE_COMPLETION_COMMIT`
constant to both `_demo_test.py` files and re-anchor the **seven** named
real-repository tests to it (fixed commit, not `"HEAD"`/worktree — revision
4 B1, plus the sixth test revision 5 B1 adds, plus the seventh —
`test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`,
`workflow-v2-1-core` item 352's own bound ledger evidence — revision 6 B1
adds), repairing both re-anchored manifest-verification loops'
worktree-vs-commit cross-source comparison with the new `_blob_at_commit`
helper (revision 5 B2/I1; disclosure: this also re-anchors
`test_real_diff_since_base_commit_classifies_exhaustively`, item 359's own
bound evidence, from an open-ended live-tree assertion to a fixed-history
one, revision 6 I1 — live coverage for the active item moves to the new
active-work-item-scoped test below); grandfather
`27f051eba897d77c742ead8b160ed519c0671ee4` in
`_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS` (**re-derived at the
actual plan-approval commit, revision 13 round 11 I1**:
`test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set`
scans `BASE_COMMIT..HEAD` at live `HEAD` and asserts set equality against
the grandfathered set, so every commit landing between now and CP1 is in
its scope — including the plan-approval commit itself, written by
`/approve-review` step 6.4, which (unlike `/milestone-implement`'s and
`/bootstrap-workflow-v2`'s own commit steps) carries no "trailers must be
the commit message's final paragraph" requirement and can therefore
reproduce the same defect this item's own `base_commit` already has (41 of
211 commits in `162154d3..HEAD` violate it, measured; one prior
plan-approval commit is among them). Before this step's own verification
runs, re-scan `162154d3..<the actual plan-approval commit>` for violations
outside the grandfathered set: if the plan-approval commit, or any other
commit landing in that window, introduces one, grandfather it here too
before proceeding; if none appears, the single `27f051eb` entry above is
already the complete set and no further action is needed); add the four new
tests (the active-work-item-scoped changed-set implementation-stage
classification test and the standalone plan-stage classification-gap
assertion at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT`, revision 4 missing
tests; the no-live-anchor static-conformance test — scan-based, gated on
the test passing rather than a hand-checked list (revision 6 missing
tests), corrected to a by-keyword-or-position property covering all nine
real call sites (revision 7, round 6 B1/missing tests; nine, not eight —
revision 8, round 7 I2), resolved via `inspect.signature(fn).bind_partial(...)`
against each imported callee rather than a hand-maintained positional-index
map, with no allowlist and a per-scanned-function negative control
(revision 8, round 7 B1/missing tests) — and the hermetic cross-source-hazard
regression test, revision 5 missing tests) — all per "Revision 6"/"Shared
wiring"'s "Prerequisite" bullet above — and confirm
`workflow_fingerprint_demo_test.py`/`workflow_state_demo_test.py`
(including `TestReconciliationTableLedgerStatusAgreement`, revision 6
missing tests) are green (**baseline restated, revision 12 round 10 I2,
superseding the `failures=1, errors=3`/`failures=4, errors=3` — "seven
failing tests" — figures revision 5 O2/revision 6 B1 recorded: those
figures predate "Revision 11"'s two newly-discovered untracked files and
no longer match measurement. Re-measured directly at revision 11's own
`HEAD` (`27f051eba897d77c742ead8b160ed519c0671ee4`, this item's own
`base_commit`, over the full file in both cases)**: `failures=3, errors=3,
skipped=4` in the former — the three errors
(`test_demonstration_against_real_repo`,
`test_142_generalized_resolver_reproduces_the_migrated_digest_not_a_hardcoded_literal`,
`test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas`)
and all three subTest failures inside
`test_real_diff_since_base_commit_classifies_exhaustively` (one per
unclassified path: `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`,
`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`,
`docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg`) share B1's
single root cause and are exactly what this step's own three-addition
widening above (revision 12 round 10 B1) and the `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT`
re-anchoring below both resolve; the four skips are pre-existing bundle/
manifest fixtures this checkout does not have (`.ai-review/current`,
unrelated to this item) and stay skipped — and `failures=1, errors=1` in
the latter: the error
(`test_real_implementation_stage_classification_has_no_unclassified_dirty_path`)
is B1's same root cause on the dirty (not diff-since-base) set, resolved
by the same widening; the failure
(`test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set`)
is the pre-existing `27f051eb` trailer lookalike this step's grandfather
entry (above) resolves. The three tests revision 6's now-superseded figure
called "originally-failing" —
`test_real_plan_approval_is_current`,
`test_real_implementing_entry_is_reachable_at_current_head`, and
`test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`
— already pass at this baseline, since `workflow-v2-1-core` has since
reached `MILESTONE_COMPLETE`; they still take the fixed-commit
re-anchoring below (per "Revision 6" (B1)'s bound-evidence disclosure) so
they stay green for the same reason regardless of what happens at any
later `HEAD`, not because they are red today. **Pass condition (checkable,
revision 13 round 11 O1, superseding revision 12 round 10 I2's version of
this sentence/required acceptance criterion 4)**: the actual plan-approval
commit is by construction a new commit on top of
`27f051eba897d77c742ead8b160ed519c0671ee4` — `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`
is committed only at that commit, which precedes `IMPLEMENTING`/CP1 entirely
by construction (`D-Approval-Commits`) — so re-measure both suites, and
re-scan the trailer-lookalike observed set per the grandfather entry above,
at that actual commit before this step's own edits, unconditionally, and
confirm the delta this step introduces is exactly the six failing tests
named above clearing (eight reported failures/errors: three errors plus
three subTest failures in the fingerprint suite, one error plus one failure
in the state suite) — not an unqualified "green" claim disconnected from a
specific measurement.
**Why this prerequisite step stays inside CP1 rather than splitting into
its own CP0 (revision 7, round 6 Usability concerns, non-numbered, not
required)**: the step's own gate ("both `_demo_test.py` suites green at
the plan-approval commit") is already measured and reported before any
command-file work begins (immediately above), which is the concrete
benefit a separate CP0 would add; splitting it would cost a registry
regeneration and a second commit/verification cycle for a two-checkpoint
item that is otherwise deliberately kept small (`session_target: 1` each),
for no gate this document does not already get by measuring the
prerequisite's own baseline explicitly, as it does above.
Then: create `.claude/commands/review-implementation.md`
per the design above (including step 4's commit-source
`compute_review_content_id_implementation_stage_at_commit`/`base`/`head`
requirement, revision 4 I1; the
`load_implementation_stage_classification`/`artifacts_path_for_work_item`
requirement; step 3's flat/scoped `<bundle_dir>` hazard sentence, revision
2; step 4's absent/unreadable-bundle clean-refusal sentence, revision 6 I2;
step 3/4's implementation-stage `MANIFEST.md` precondition and
stale-plan-stage-manifest-variant sentences, and step 2/"Independent
inspection"'s narrowed version-independence claim, revision 7 B2; and step
4's `workflow_state.approval_review_content_id` citation, revision 7 O1);
register it in `REVIEW_SUBJECT_ROSTER`
(`review-implementation.md: "bundle"`); add it to `EXPECTED_CALL_SITES`,
renaming `TestAssertLocalGenerationMatchesCallSiteConformance`'s
test/docstring to name three live callers (revision 2, I2); add its
`TestReviewSubjectDeclarationsLive` entries (renaming "thirteen" →
"fourteen", and its class docstring's "nine-consumer" phrase toward
"eleven-consumer", revision 6 O2); add its static-conformance tests,
including the `artifacts_path_for_work_item`-textual-presence,
`MissingRequiredBundleFileError`-textual-presence (revision 7, round 6
missing tests), and frontmatter (`description:`/`argument-hint:`) checks
(revision 2); add its
golden hash; update `assert_bundle_not_rejected`'s docstring and the other
three I2 documentation sites (`assert_local_generation_matches`'s
docstring, `WorktreeOrHeadMismatchError`'s docstring, `REVIEW_PROTOCOL.md`'s
"Generation diagnostic metadata" section); add its `MILESTONE_WORKFLOW.md`
"Allowed actions" bullet. Commit this checkpoint's changes, **then** — not
before (revision 4, round 3 I2: `REVIEW_SUBJECT_ROSTER` is read live from
the working tree but `discover_review_subject_declarations` resolves each
entry's content at `HEAD`, so running before the commit fails closed on
`review-implementation.md` not yet existing at `HEAD`) — run the full
existing Python suite, including `workflow_state_demo_test.py` (explicitly
including `TestReconciliationTableLedgerStatusAgreement`, revision 6
missing tests) and `workflow_fingerprint_demo_test.py` (revision 2, I4 —
both are real-repository checks this change affects, per
`bootstrap-workflow-v2.md`'s own "run `_demo_test.py` if a real-repository
check applies" standard), to confirm zero regressions (an achievable,
unambiguous pass condition now that the prerequisite step above and the I2
grandfather entry have landed) before moving to CP2.

**CP2** — create `.claude/commands/review-functional.md` per the design
above (including step 6's `assert_bundle_not_rejected` call, revision 2);
register it in `REVIEW_SUBJECT_ROSTER` (`review-functional.md: "bundle"` —
revised from `"none"`, revision 2, B2); add its `TestReviewSubjectDeclarationsLive`
entries (renaming "fourteen" → "fifteen"; `len(exempt)` **stays 4** — not
5, revision 2 correction — since `review-functional.md` is not `"none"`;
add `review-functional.md: 1` to `EXPECTED_ASSERTION_COUNT`); add its
static-conformance tests, including frontmatter checks (revision 2; not a
duplicate `assert_bundle_not_rejected`-exactly-once check — dropped,
revision 3, round 2 missing tests, since `EXPECTED_ASSERTION_COUNT` already
covers that); **two new textual-presence checks (revision 10, round 9
missing tests, pinning the I1/I2 fixes above — required acceptance
criterion 4)**: one asserting `review-functional.md` states a single
coherent untracked-item policy — the file's text contains its step 1 clean
refusal and does not contain the phrase "read the checklist" (dropped by
the I1 fix; a regression would reintroduce the contradictory branch) — and
one asserting the file's functional-acceptance wording is work-item-neutral
— the file's text contains "checklist's required functional flows" and does
not contain the bare phrase "the Android app" (a regression would
reintroduce the app-specific rule the I2 fix removed); add its golden hash; add its
`MILESTONE_WORKFLOW.md` "Allowed actions" bullet plus the "Hard gates
summary" clarifying sentence; add the `REVIEW_PROTOCOL.md` "Local reviewer
commands" section, including the differently-shaped-reports sentence
(revision 2); bring `CLAUDE.md`'s command list fully current, all sixteen
names (revision 2, I5). Commit this checkpoint's changes, **then** — not
before, same reasoning as CP1 above (revision 4, round 3 I2) — run the full
existing Python suite
(`workflow_fingerprint_test.py`, `workflow_fingerprint_generalization_test.py`,
`workflow_state_test.py`, `workflow_state_completion_obligations_test.py`,
`workflow_integration_test.py`, `workflow_test_harness_test.py`,
`workflow_state_demo_test.py` — including `TestReconciliationTableLedgerStatusAgreement`,
revision 6 missing tests — and `workflow_fingerprint_demo_test.py` —
revision 2, I4) to confirm zero regressions before self-review.

No migration, no Room schema change, no Android app involvement — this is
process tooling only, exactly like `workflow-v2-1-core`'s own scope.

## Self-review (`SELF_REVIEWING_PLAN`)

- **Missing requirements?** All eight requirements from the user's brief
  map to at least one checkpoint (traceability table above); both
  checkpoints map to at least one requirement (D3's bidirectional coverage,
  validated by `generate_mapping` at generation time).
- **Migration risk?** None — no Room/schema/backup-format change; this
  plan touches only `.claude/commands/*.md`, `scripts/*.py`, three
  documentation files (`MILESTONE_WORKFLOW.md`, `REVIEW_PROTOCOL.md`,
  `CLAUDE.md`), and (revision 3, B1/B2) two artifacts-declaration JSON files
  (`workflow-v2-3-artifacts.json`, `workflow-v2-1-core-artifacts.json`).
- **Usability gaps?** Considered and addressed: both commands state
  plainly, in their own final step, that they write nothing and name the
  exact commands that remain authoritative, so a user can never mistake an
  advisory report for a recorded review round. Considered and rejected:
  writing the report to a dedicated advisory file (e.g.
  `<bundle_dir>/LOCAL_REVIEW_NOTES.md`) instead of only the chat response —
  rejected because it would invent a new artifact-location convention this
  small release doesn't need, and a printed report the user can copy
  verbatim already satisfies the same ergonomic goal without adding a new
  file type to the protocol.
- **Unnecessary complexity?** Considered and rejected: giving these
  commands their own recorded ledger stage (mirroring
  `plan_review_stages`) — explicitly what requirements 4 and 6 rule out for
  this release, and what "Deferred to Workflow v3" below defers instead.
  Considered and rejected: a `governing_workflow_version` guard on either
  new command — unlike `/review-plan`, both target states are already
  version-uniform, so a guard would only ever refuse work a `"1"` item is
  entitled to as much as a `"2.1"` one.
- **Missing tests?** The plan's own "Shared wiring" section enumerates
  every existing closed-roster test this change must touch to stay green
  (`REVIEW_SUBJECT_ROSTER`/`TestReviewSubjectDeclarationsLive`,
  `EXPECTED_CALL_SITES`, `TestGoldenCommandFileHashes`), found by direct
  inspection of the test files rather than assumed, plus new
  static-conformance coverage for both commands' own key invariants
  (revision 2 adds: `review-implementation.md` names
  `artifacts_path_for_work_item`; both files carry
  `description:`/`argument-hint:` frontmatter — revision 3 drops the
  duplicate `review-functional.md` `assert_bundle_not_rejected`-exactly-once
  assertion, round 2 missing tests, since `EXPECTED_ASSERTION_COUNT` already
  covers it). Revision 1's verification lists for both checkpoints omitted
  `workflow_state_demo_test.py` and `workflow_fingerprint_demo_test.py`
  despite editing a test class in the former directly — corrected in the
  checkpoint registry section above (revision 2, I4). **Revision 3 addition
  (round 2, B1's own missing-test gap)**: CP1 also adds a
  `workflow_state_demo_test.py` real-repository test keyed on the *active*
  work item's own artifacts path rather than the hardcoded
  `workflow-v2-1-core` default, so a future item's own under-widened
  implementation-stage declaration is caught by the test suite itself,
  the way this item's own B1 defect should have been. **Revision 4
  correction (round 3, missing tests)**: that test is changed from a
  dirty-set check (`any_protected_path_dirty`, which cannot observe a path
  that is already committed by the time it matters) to a changed-set check
  over `base_commit..HEAD`, per "Revision 4" above — the dirty-set form is
  kept too, as a distinct, real pre-commit safety gate, not replaced. CP1
  also adds a standalone assertion that
  `assert_all_changed_paths_classified_commit` raises nothing over
  `workflow-v2-1-core`'s own fixed `base..completion` range, naming B1's
  own failure class directly rather than only as a precondition inside a
  digest computation. **Revision 5 addition (round 4, missing tests)**: CP1
  also adds a static-conformance test asserting every `_demo_test.py` call
  site into `approval_is_current`/`implementing_entry_reachable`/
  `compute_review_content_id_*_at_commit*` passes an explicit
  `head=`/`commit=` keyword argument (with a named allowlist for the one
  deliberately live-tree call) — the cheap textual check that would have
  caught this round's own sixth-test gap (B1) immediately — and a hermetic
  `scripts/workflow_fingerprint_test.py` regression test, using the
  existing `ScratchRepo` fixture, pinning the *class* of cross-source
  comparison defect B2/I1 fix rather than only this repository's own
  instance of it. **Revision 6 addition (round 5, missing tests)**: CP1's
  and CP2's own stated verification now names
  `TestReconciliationTableLedgerStatusAgreement` explicitly, alongside the
  two `_demo_test.py` files already named — the one suite that observes a
  change to bound completion-ledger evidence (this round's own B1/I1), and
  the slowest single class in the file — so the gate reads as visibly
  self-checking rather than incidentally sufficient; and the
  explicit-`head=`/`commit=` static-conformance test's own gate is stated
  as "the test passing," never a hand-checked tally of known call sites,
  since a hand-enumerated list of this exact call set has now come up short
  three consecutive rounds running. **Revision 7 correction (round 6,
  B1/missing tests)**: the "explicit `head=`/`commit=` keyword argument"
  property itself was wrong — it both over-fires (flags an already-correct
  call that passes the anchor positionally,
  `workflow_state_demo_test.py:634`) and under-fires (would pass an
  explicit `head="HEAD"`, the actual defect class) — corrected to "never
  resolves the anchor to live `"HEAD"`/the default, by keyword or
  position," with `:634` named as an eighth, already-conforming call site
  and the named allowlist dropped (it exempted a call the corrected
  property's own function-name scoping was never going to reach). Also
  given a negative control (asserts the scan flags a synthetic
  non-conforming call and that its real-call-site count is non-zero) and
  prototyped against the current tree with its output pasted, rather than
  left as a specification only. **Revision 10 addition (round 9 missing
  tests)**: CP2's own static-conformance tests also gain the two
  textual-presence checks named in the "Checkpoint registry" section above,
  pinning the untracked-item-policy and work-item-neutral-wording fixes
  (round 9 I1/I2 — required acceptance criterion 4).
- **Artifacts-declaration template fit** (required check per
  `docs/ai-workflow/MILESTONE_WORKFLOW.md`'s `SELF_REVIEWING_PLAN` entry).
  **Revision 3 correction (round 2, B1/B2)**: revision 2's claim here was
  wrong — the generated declaration's **plan-stage** classification is
  functionally correct for this item's own plan footprint (confirmed above,
  "Independent inspection"), but its **implementation-stage** classification
  was not: it classified nothing but its own self-reference, failing closed
  on every real path this item's checkpoints touch. CP1's new first step
  (this document's "Revision 4"/"Shared wiring" sections above) widens it
  explicitly, with a stated protected/excluded split and per-entry
  rationale, before any implementation bundle is generated — not accepted
  as-is. The measured, pre-existing real-repository-suite regression this
  same defect class caused (B2, five failing tests) is likewise recorded
  and given a stated remedy there rather than left as an unachievable
  verification gate. **Revision 4 correction (round 3, B1/B2)**: revision
  3's own remedy for the underlying real-repository-suite regression was
  itself measured, this round, to not survive this item's own
  plan-approval commit — the round's own two Blocking findings, both
  addressed in "Revision 4" above by re-anchoring the five affected
  real-repository tests to `workflow-v2-1-core`'s own fixed completion
  commit rather than to a moving `"HEAD"`, and by adding the two paths B2
  found missing (`docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`,
  `docs/ACTIVE_MILESTONE.md`) to this item's own
  `implementation_stage.excluded_paths`. **Revision 5 correction (round 4,
  B1/B2/I1/I2)**: revision 4's own remedy was itself measured, this round,
  to be short by a sixth affected test and to introduce a fresh
  worktree-vs-commit cross-source comparison defect into two of the five —
  both addressed in "Revision 5" above (the sixth test's own `head=`
  treatment, and the new `_blob_at_commit` helper anchoring both
  manifest-verification loops to the same fixed commit as the manifest
  itself). The implementation-stage declaration was also confirmed, this
  round, to be narrower than both comparable declarations' own established
  concurrency-hardening posture — "Revision 5" (I2) widens it to match,
  before any implementation bundle is generated, so a concurrent write
  elsewhere in the repository never staled this item's own approval.
  **Revision 6 correction (round 5, B1)**: revision 5's own remedy was
  itself measured, this round — over the **full** `workflow_state_demo_test.py`,
  not the narrower `TestAgainstRealRepository` subset every prior round's
  own measurement used — to be short by a seventh affected test,
  `test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit`,
  living in a different test class
  (`TestCheckpointOriginationAgainstRealRepository`) than the other six.
  Addressed in "Revision 6" above by the same fixed-anchor `head=` treatment
  the other six already get; independently confirmed (three-stage clone
  measurement, "Revision 6" above) to be the last such gap — the full file
  runs `Ran 42 tests ... OK` once all seven are fixed and committed.
  **Revision 9 correction (round 8, B1)**: this entry's own earlier claim
  that the generated declaration's plan-stage classification is
  "functionally correct for this item's own plan footprint" was true but
  not the standard a fail-closed classifier imposes once concurrent
  writers exist — the same standard "Revision 2" (B1) already rejected one
  stage over. Measured directly this round: `resolve_plan_stage_metadata(repo_root,
  "workflow-v2-3")` left 9 of 464 tracked files unclassified, three of them
  on the protected side (`docs/TECHNICAL_DECISIONS.md`,
  `docs/ai-workflow/WORKFLOW_V2_PLAN.md`,
  `docs/ai-workflow/WORKFLOW_V2_AUDIT.md` — all three live, tracked files
  covered only by `workflow-v2-1-core`'s own `plan_stage.protected_paths`,
  never by this item's), so a concurrent edit to any of them (most
  plausibly `docs/TECHNICAL_DECISIONS.md`, which carries ten open decision
  rows and is the file `CLAUDE.md` routes every toolchain decision to) hard-blocked
  this item's own plan gate with `UnclassifiedPathError`. "Revision 9" (B1)
  above adds the three as this item's own `plan_stage.excluded_paths`
  entries, closing the gap; the six remaining unclassified files
  (`.editorconfig`, `build.gradle.kts`, `gradle.properties`, `gradlew`,
  `gradlew.bat`, `settings.gradle.kts`) are unclassified under both this
  item's and `workflow-v2-1-core`'s plan stage alike — a pre-existing,
  shared, deliberately-unfixed template gap, not something this item
  introduced or takes on.
- **Open decisions**: `docs/TECHNICAL_DECISIONS.md`'s "Open decisions"
  table checked in full; none of its ten rows are touched or silently
  finalized by this plan.

## Deferred to Workflow v3 (not planned here)

- A recorded local-review ledger stage for the implementation and
  functional gates (analogous to the plan stage's `plan_review_stages`),
  if real usage of `/review-implementation`/`/review-functional` in this
  release shows the advisory-report-only shape is insufficient.
- Controller-aware lifecycle states distinguishing a local-model reviewer
  pass from a manual external reviewer pass at the implementation/
  functional gates, the way `D-Plan-Review-Stages` already does for the
  plan gate.
- Any adopt/reject decision on richer local-vs-external reviewer
  orchestration generally — deferred pending real operator usage of this
  release's two simple commands, the same "collect a real baseline before
  changing further" discipline `WORKFLOW_V2_PLAN.md`'s own "Workflow v2.2
  handoff" note already recommends for its (differently-scoped) deferred
  work.
