# Workflow v2.x — System Audit

Salvage audit of Workflow v2.x as one system, performed on branch
`workflow/system-audit-repair` against the frozen baseline
`workflow-v2.3.0` (`9b171a0d591400250fa8f4e3fe28dd9a7d706c2b`) plus the
in-flight `workflow-v2-3-1` hotfix commits.

**Governance note.** Workflow v2.x is the system under test and is *not*
used to govern this campaign. No `/milestone-plan`, `/review-plan`,
`/approve-review`, `/milestone-implement`, `/apply-implementation-review`,
`/prepare-functional-review`, `/apply-functional-review`,
`/accept-milestone` or sibling command is run against this worktree as a
governance mechanism. Equivalent command/script paths *are* executed, but
only inside disposable scratch repositories built by
`scripts/workflow_acceptance_matrix_test.py`.

Companion documents:

- `WORKFLOW_DEFECT_LEDGER.md` — every candidate issue, its proof, and its
  disposition.
- `WORKFLOW_ACCEPTANCE_MATRIX.md` — the end-to-end scenarios the repaired
  system must prove, and how each is executed.
- `WORKFLOW_REPAIR_PLAN.md` — repairs clustered by root cause.

---

## 1. Independent lifecycle / state-machine model

Reconstructed from `scripts/workflow_state.py`,
`scripts/workflow_fingerprint.py`, `scripts/prepare-ai-review.sh` and
`.claude/commands/*.md` — not from plan prose.

### 1.1 Work-item vocabulary

| Concept | Source of truth | Legal values |
|---|---|---|
| `work_item_type` | `workflow_state.WORK_ITEM_TYPES` / `workflow_fingerprint.WORK_ITEM_TYPES` | `process`, `product` |
| `work_item_kind` | `workflow_state.WORK_ITEM_KINDS` | `process`, `product`, `synthetic` |
| `governing_workflow_version` | fixed at creation from `WORKFLOW_CONFIG.json.default_workflow_version` | `"1"`, `"2.1"` |
| `phase` | `workflow_state.KNOWN_PHASES` (allowlist, not a transition graph) | 17 values |
| checkpoint status | `workflow_state.CHECKPOINT_STATUSES` | `IN_PROGRESS`, `COMPLETE` |
| approval status/basis | `APPROVAL_STATUSES` / `APPROVAL_BASES` | `CURRENT`/`STALE`; `EXTERNAL_APPROVE`/`USER_OVERRIDE`/`LEGACY_V1` |
| approval stage | `APPROVAL_STAGES` | `plan`, `implementation`, `acceptance` (a fourth, `scoped_remediation`, was removed with its command — ledger `I10`) |
| plan-review verdict | `PLAN_REVIEW_VERDICTS` | `APPROVE`, `REVISE`, `BLOCK` |
| bundle-generation outcome | `record_bundle_generation` | `ordinary`, `same_content` |
| generation-record role | `_bundle_generation_record_role` | `ordinary` (2 trailers), `recovered` (3 trailers) |

### 1.2 Phases that are actually written

`KNOWN_PHASES` is an allowlist of 17 values. Only 13 are ever written by
a state-writer function:

| Phase | Writer |
|---|---|
| `PLANNING` | `default_work_item` |
| `AWAITING_EXTERNAL_PLAN_REVIEW` | `publish_plan_revision` (`"1"` items) |
| `AWAITING_LOCAL_PLAN_REVIEW` | `publish_plan_revision` (`"2.1"`), `transition_to_awaiting_local_plan_review` |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `record_local_plan_review` (APPROVE) |
| `REVISING_PLAN` | `record_local_plan_review` / `record_manual_plan_review` (REVISE) |
| `AWAITING_PLAN_APPROVAL` | `record_manual_plan_review` (APPROVE) |
| `IMPLEMENTING` | `apply_plan_approval` (`apply_scoped_remediation_acceptance` was a second writer until ledger `I10` retired it) |
| `SELF_REVIEWING_IMPLEMENTATION` | `complete_checkpoint` (last checkpoint only); `enter_self_reviewing_implementation` (from `IMPLEMENTING` when every checkpoint is already `COMPLETE` — added in convergence pass 7, ledger `B8`) |
| `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `record_bundle_generation` (both outcomes) |
| `APPLYING_REVIEW_FEEDBACK` | `enter_applying_review_feedback` |
| `AWAITING_FUNCTIONAL_REVIEW` | `apply_technical_approval`, `promote_legacy_work_item` |
| `LEGACY_READY` | `import_legacy_work_item` |
| `MILESTONE_COMPLETE` | `complete_work_item` |

Never written by any writer: `SELF_REVIEWING_PLAN`,
`FIXING_FUNCTIONAL_FINDINGS`, `AWAITING_TECHNICAL_APPROVAL`,
`AWAITING_USER_ACCEPTANCE`. See ledger `O1` for the disposition — these
are narrative state names in `MILESTONE_WORKFLOW.md` with no persisted
representation; no code path or command tests for them, and
`milestone_complete_gate_reachable` already documents the
`AWAITING_USER_ACCEPTANCE` case as a known, accepted gap.

### 1.3 Transition graph as implemented

```
PLANNING
  └─ publish_plan_revision ─▶ AWAITING_LOCAL_PLAN_REVIEW        ("2.1")
                             AWAITING_EXTERNAL_PLAN_REVIEW      ("1")

AWAITING_LOCAL_PLAN_REVIEW
  ├─ record_local_plan_review APPROVE ─▶ AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW
  ├─ record_local_plan_review REVISE  ─▶ REVISING_PLAN
  └─ record_local_plan_review BLOCK   ─▶ (no-op, state returned unchanged)

AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW
  ├─ record_manual_plan_review APPROVE ─▶ AWAITING_PLAN_APPROVAL
  ├─ record_manual_plan_review REVISE  ─▶ REVISING_PLAN
  └─ record_manual_plan_review BLOCK   ─▶ (no-op)

REVISING_PLAN
  └─ transition_to_awaiting_local_plan_review ─▶ AWAITING_LOCAL_PLAN_REVIEW

AWAITING_PLAN_APPROVAL
  └─ apply_plan_approval ─▶ IMPLEMENTING

IMPLEMENTING
  ├─ transition_checkpoint_in_progress  (phase unchanged)
  ├─ complete_checkpoint ─▶ IMPLEMENTING | SELF_REVIEWING_IMPLEMENTATION
  └─ enter_self_reviewing_implementation ─▶ SELF_REVIEWING_IMPLEMENTATION
        (only when every registry checkpoint is already COMPLETE;
         no-op if already there; refuses otherwise. Pass 7, `B8`)

SELF_REVIEWING_IMPLEMENTATION
  └─ record_bundle_generation(stage="implementation") ─▶ AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW

AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
  ├─ enter_applying_review_feedback ─▶ APPLYING_REVIEW_FEEDBACK
  ├─ apply_implementation_provenance_recovery (phase value unchanged)
  └─ apply_technical_approval ─▶ AWAITING_FUNCTIONAL_REVIEW

APPLYING_REVIEW_FEEDBACK
  └─ record_bundle_generation(stage="post-fix") ─▶ AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW

AWAITING_FUNCTIONAL_REVIEW
  ├─ mark_technical_approval_stale (phase unchanged)
  ├─ record_bundle_generation(stage="post-fix", requires STALE) ─▶ AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
  └─ complete_work_item ─▶ MILESTONE_COMPLETE
     (a third edge, apply_scoped_remediation_acceptance ─▶ IMPLEMENTING,
      existed until ledger I10 retired it; its own entry precondition had
      no producer, so the edge was never traversable in practice)

LEGACY_READY
  └─ promote_legacy_work_item ─▶ AWAITING_FUNCTIONAL_REVIEW
```

### 1.4 Identity fields — meaning, writer, reader

| Field | Lives in | Sole writer | Meaning |
|---|---|---|---|
| `plan_revision` | registry JSON (authoritative) + `WORKFLOW_STATE.json` (mirror) | `write_registry_and_mapping` + `publish_plan_revision` | which plan round is being reviewed; cross-checked against the plan document's `(Revision N)` title by `load_plan_revision`; mirror equality enforced by `validate_state` and by `prepare-ai-review.sh`'s plan-stage preflight |
| `implementation_revision` | `WORKFLOW_STATE.json` | `record_bundle_generation` (`ordinary` only) | which implementation round is being reviewed; never part of either hashed projection |
| `reviewed_implementation_head` | `WORKFLOW_STATE.json` | `record_bundle_generation` (`ordinary` only) | the commit whose protected implementation content the current round reviews. Always the *parent* of the round's generation-record commit under the documented protocol |
| generation head | `MANIFEST.md` (`generation_head:`) | the manifest writers | the commit `review_content_id` was measured at, and the worktree HEAD at generation time |
| `base_commit` | `WORKFLOW_STATE.json` | `route_work_item` (immutable once set) | the diff origin for both fingerprint projections and for every trailer discovery window |
| `review_content_id` | recomputed; recorded in `MANIFEST.md`, `REVIEW_REQUEST.md`, approval records, `plan_review_stages` | `compute_review_content_id_*` | digest over the stage's protected-content projection **plus the classification sets themselves** |
| `bundle_id` | `MANIFEST.md` | `compute_bundle_id` | digest over the whole bundle directory's bytes |
| `current_bundle_id` | `WORKFLOW_STATE.json` | *nothing* — permanently `null` | vestigial field; see ledger `O2` |

### 1.5 The three generation modes

| Mode | Trigger | State effect | Commit shape |
|---|---|---|---|
| **ordinary** | `resolve_bundle_generation_outcome` → `("ordinary", None)` | `reviewed_implementation_head = head`, `implementation_revision += 1`, `phase → AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | 2 trailers: `Workflow-Bundle-Generation-Record: <id>/<rev>`, `Workflow-Work-Item` |
| **same_content** | `resolve_bundle_generation_outcome` → `("same_content", t)` | only `phase`/`state_revision`/`last_transition` | 3 trailers: the *unchanged* `Workflow-Bundle-Generation-Record` value, `Workflow-Work-Item`, `Workflow-Supersedes: <t>` |
| **recovered** | `/recover-implementation-provenance` (from `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) | only `state_revision`/`last_transition` (phase value unchanged) | same 3-trailer shape |

`same_content` and `recovered` are the same *commit role* and share
`_classify_generation_record_interval`; they differ only in which phase
may invoke them and whether `phase` transitions.

---

## 2. Command → Python dependency map

| Command | State writers it calls | Fingerprint/script entry points | Gate predicates |
|---|---|---|---|
| `/milestone-plan` | `route_work_item`, `write_registry_and_mapping`, `generate_artifacts_declarations`, `publish_plan_revision` | `resolve_bundle_dir(..., stage="plan")`, `prepare-ai-review.sh … plan <id>`, `assert_bundle_not_rejected` | `identity_reference_admits` |
| `/review-plan` | `record_local_plan_review` | `resolve_bundle_dir(..., stage="plan")`, `compute_bundle_id`, `compute_review_content_id_plan_stage_for_work_item`, `assert_local_generation_matches`, `assert_bundle_not_rejected` | `validate_local_plan_review_preconditions` |
| `/record-manual-plan-review` | `record_manual_plan_review` | same as above + `parse_review_feedback_binding_fields` | `validate_manual_plan_review_preconditions`, `check_manual_stage_bundle_id_advisory` |
| `/apply-plan-review` | `publish_plan_revision`, `transition_to_awaiting_local_plan_review`, `write_registry_and_mapping` | `resolve_bundle_dir(..., stage="plan")`, `prepare-ai-review.sh … plan <id>`, `assert_feedback_matches_bundle`, `assert_bundle_not_rejected` | — |
| `/approve-review plan` | `apply_plan_approval` (via the journal/guard transaction), `take_over_plan_approval_transaction` (target-checked) | `resolve_bundle_dir(..., stage="plan")`, `resolve_plan_stage_approval_commit_paths`, `compute_review_content_id_plan_stage_for_work_item`, `assert_local_generation_matches`, `assert_bundle_not_rejected` | `plan_approval_gate_reachable`, `resolve_approval_basis`, `validate_user_confirmation` |
| `/milestone-implement` | `transition_checkpoint_in_progress`, `complete_checkpoint`, `enter_self_reviewing_implementation`, `record_bundle_generation` | `resolve_bundle_dir` (no stage — the compatibility rule), `prepare-ai-review.sh … implementation`, `assert_bundle_not_rejected` | `implementing_entry_reachable`, `select_next_checkpoint`, `resolve_checkpoint_ownership` |
| `/review-implementation` | *(none)* | `compute_review_content_id_implementation_stage_at_commit`, `assert_local_generation_matches`, `assert_feedback_not_owned_by_other_work_item`, `assert_bundle_not_rejected` | — |
| `/apply-implementation-review` | `enter_applying_review_feedback`, `record_technical_review_block_pin`, `record_bundle_generation` | `resolve_bundle_generation_outcome`, `prepare-ai-review.sh … post-fix`, `assert_bundle_not_rejected` | `assert_feedback_matches_bundle` |
| `/recover-implementation-provenance` | `apply_implementation_provenance_recovery` | `verify_implementation_provenance_recovery`, `prepare-ai-review.sh … <stage>` | `validate_implementation_provenance_recovery_confirmation` |
| `/approve-review implementation` | `apply_technical_approval`, `record_technical_review_block_pin` | `compute_review_content_id_implementation_stage_at_commit`, `assert_local_generation_matches`, `assert_bundle_not_rejected` | `technical_approval_gate_reachable`, `implementation_provenance_interval_reachable`, `any_protected_path_dirty`, `is_technical_review_block_pinned`, `resolve_approval_basis` |
| `/prepare-functional-review` | `promote_legacy_work_item` | `discover_current_functional_checklist_evidence` | — |
| `/review-functional` | *(none)* | `assert_bundle_not_rejected` | — |
| `/apply-functional-review` | `mark_technical_approval_stale`, `record_bundle_generation`, `create_remediation_child_work_item` | `assert_functional_review_not_already_consumed`, `mark_functional_review_consumed`, `resolve_bundle_generation_outcome`, `prepare-ai-review.sh … post-fix` | — |
| `/accept-milestone` | `complete_work_item` | — | `milestone_complete_gate_reachable`, `resolve_own_registry_completion_status`, `resolve_completion_obligations` |
| `/prepare-review` | *(none)* | `prepare-ai-review.sh` | — |
| `/bootstrap-workflow-v2` | one-time bootstrap driver | — | — |

---

## 3. Root-cause clusters

See `WORKFLOW_REPAIR_PLAN.md` for the repairs. The clusters found:

**C1 — Round identity keyed on the generation head instead of the
reviewed round.** `prepare-ai-review.sh`'s round-identity preflight
(GPT-R43-001) infers "this is a new round" from "HEAD differs from the
previous bundle's recorded head". That inference is false for both
non-ordinary generation modes, which deliberately move the generation
head while pinning `implementation_revision`. Every path that produces a
recovered-role commit is therefore unable to publish its own bundle.
Manifestations: `B1` (same-content republication), `B2`
(`/recover-implementation-provenance`), `B3` (functional bounded-fix
same-content). One rule, three reachable commands.

**C2 — `MANIFEST.md` reuses the name `reviewed_implementation_head` for a
different value than `WORKFLOW_STATE.json` does.** The manifest field is
the generation head; the state field is the reviewed round's head. They
differ by exactly one commit on every ordinary round and by more after a
same-content round. This is what made C1's rule look plausible.

**C3 — Generated artifact declarations are incomplete in three
independent ways.** `generate_artifacts_declarations` inherits a
mature plan-stage classification but emits an implementation-stage
classification that names nothing except its own file, and never sees
`work_item_type`, so it could not produce a type-appropriate one even in
principle. Every work item created strictly through the sanctioned
generators fails closed at its first implementation-stage computation —
after the plan-approval hard gate. The plan-stage half had two further
gaps of its own: it inherits an exclusion set authored as the complement
of a *larger* protected set, leaving three repository documents in
neither (`B5`), and it names no sibling work item's plan-stage content,
so a second concurrent work item — `D1`'s explicit normal case — fails
every other item's classification closed (`B7`).

**C4 — Commands do not name the complete author-written input set the
generator hard-requires.** `finalize_bundle_generation` enforces
`TEST_RESULTS.md`'s plan-stage marker lines and
`IMPLEMENTATION_SUMMARY.md`'s `implementation_revision:` line, and
*withdraws* the bundle on a miss. `/milestone-plan`, `/apply-plan-review`,
`/milestone-implement` and `/apply-implementation-review` never mention
those lines.

**C5 — Command prose that predates the generator taking a file over.**
`PLAN.md` is derived from a pinned snapshot by the generator (WFR-67),
but two commands still instruct the operator to author it.

**C6 — Bootstrap fragility for a repository that does not already have
the workflow's directory layout.** `write_registry_and_mapping` writes
into `docs/ai-workflow/registry/` and `.../requirements/` without
creating them.

**C7 — A fail-closed rule applied at one call site instead of at the
shared one.** `workflow-v2-3-1` CP1's own round-1 remediation added an
`isinstance(work_item_type, str)` guard to `resolve_plan_stage_metadata`'s
inline check but not to the shared `validate_work_item_type` every
implementation-stage reader goes through (`I2`). The same shape appears
in the opposite direction with `DEFAULT_ARTIFACTS_PATH`: `GPT-R30-005`
retired the equivalent defaults in one place and `OPUS-R27-002` in
another, while two live defaults survived — one of them a fail-open in
the legacy-adoption freshness check (`I6`).

**C8 — Non-ordinary lifecycle modes built helper-first and never given an
end-to-end fixture.** `same_content` and the recovered role were covered
only at the helper level; no test drove either through
`prepare-ai-review.sh`, and the one suite that did drive the script for
round identity built its state by writing `WORKFLOW_STATE.json` directly
and encoded `B1` as its specification (`I5`).

**C9 — A load-bearing precondition assumed by the code and never stated
by the command.** `workflow_state.py` twice names "`/milestone-plan`'s
own staging step" — a step that did not exist — and without it the
resolver's tracked-path requirement made a fresh work item's first plan
bundle ungeneratable (`B6`).

---

## 4. Areas audited

- Work-item typing/routing: `default_work_item`, `route_work_item`,
  `validate_work_item_type`, `resolve_plan_stage_metadata`'s type gate,
  legacy import/promotion, invalid/missing/corrupt type.
- Plan lifecycle: fresh plan, bundle generation, local/manual review
  stages, multi-round REVISE, revision publication, plan approval
  (journal/guard/staging/commit/materialize transaction), approval-commit
  discovery, stale bundle and stale review handling.
- Implementation lifecycle: checkpoint selection/claim/complete, all
  three generation modes, repeated REVISE rounds, technical approval,
  provenance-interval verification, supersession chains.
- Functional lifecycle: checklist evidence commits, bounded-fix branch,
  remediation-child branch, consumed markers, final acceptance. (Scoped
  remediation was audited in passes 1-7 and retired in pass 8 — ledger
  `I10`.)
- Recovery/resume: worktree identity, checkpoint claims/guards/takeover,
  plan-approval journal takeover, provenance recovery, `REJECTED`
  withdrawal/quarantine.
- Identity semantics: every combination in §1.4/§1.5 against the four
  independent implementations (`workflow_state.py`,
  `workflow_fingerprint.py`, `prepare-ai-review.sh`, command Markdown).
- Artifact classification: generated defaults, plan vs implementation
  stage, self-protection, concurrent/out-of-scope writes,
  product-vs-process asymmetry.
- Reviews/approvals: ledger identities, verdict atomicity, malformed
  persisted records, manifest shape, read-boundary validation, stage
  casing migration, trailer discovery and final-paragraph semantics.
- Command consistency: all 16 portable `.claude/commands/*.md` files
  against the Python they invoke.
- Test quality: which suites would have caught each defect, and which
  bypass the layer they claim to prove.

## 5. Areas audited and explicitly scoped out

- **`D-Completion-Obligations` (`WFO-STATE-SERIALIZATION`,
  `WFO-LEDGER-COVERAGE`).** Audited as a mechanism and executed at HEAD —
  `discover_state_writers` resolves a 20-file surface (13 writers, 1
  publisher, 6 non-writers) and `verify_wfo_state_serialization` returns
  `PASS` — but it is not on any live path for a new work item: only
  `workflow-v2-1-core`'s registry declares `completion_obligations`, and
  that item is `MILESTONE_COMPLETE`. A freshly created item declares
  none, so `resolve_completion_obligations` is vacuous for it.
- **`/bootstrap-workflow-v2`.** A one-time driver scoped to
  `workflow-v2-1-core`'s own bootstrap, already spent. Audited for
  state-writer conformance (it is a declared writer and conforms); its
  checklist semantics are historical, not part of the generic lifecycle.
- **Merge-topology behaviour beyond the named refusals.** This
  repository's own rules forbid merges during a work item's execution,
  and every path that walks history refuses a merge by name
  (`NonFirstParentProvenanceIntervalError`,
  `NonFirstParentFunctionalChecklistEvidenceError`,
  `AmbiguousApprovalTrailerError`). `O6` records the one residual
  short-circuit and why it is not a correctness hole in any consumer.

## 6. Verification performed

- Full Workflow Python suite at HEAD: `workflow_fingerprint_test.py` 203,
  `workflow_state_test.py` 610, `workflow_test_harness_test.py` 19,
  `workflow_integration_test.py` 181,
  `workflow_fingerprint_generalization_test.py` 79,
  `workflow_state_completion_obligations_test.py` 103 — all OK.
  (`workflow_state_test.py` gained 8 in pass 7,
  `TestEnterSelfReviewingImplementation`, reaching 653; pass 8 took it to
  610 — 43 tests that only proved the retired `/accept-scoped-remediation`
  path deleted, two gate-truth-table tests collapsed into one, and one new
  test pinning the replacement refusal message: −43 net, see §12.
  `workflow_integration_test.py` went 174 → 181 in pass 8: three
  retired-command text/trailer tests removed and the ten-test
  `TestRetiredScopedRemediationLeavesNoLiveSurface` added.)
- Both demo suites: `workflow_state_demo_test.py` 46 OK,
  `workflow_fingerprint_demo_test.py` 15 OK (4 skips).
- Acceptance matrix: `workflow_acceptance_matrix_test.py` 87 OK
  (16 skips, each a type-independent row the product subclass declines by
  name). Pass 7 added `B14`, `F3` and `F4`, rebuilt `C5`, and withdrew
  one row with its reason recorded (ledger `I10`). Pass 8 added no row and
  removed none: the withdrawn row's subject is now retired outright.
- Android gate: `./gradlew spotlessCheck detekt lintDebug
  testDebugUnitTest --rerun-tasks` — BUILD SUCCESSFUL, 45 tasks executed.
- CI now runs seven Python suites, not six: pass 7 added
  `workflow_state_completion_obligations_test.py` (ledger `O10`).
- **The campaign's originating question, answered against real history.**
  In a disposable clone of this repository at HEAD, a first real RepFlow
  *product* milestone (`milestone-9`) was created strictly through the
  sanctioned generators — `route_work_item` (with `repo_root`, so
  `identity_reference_admits` scanned all 104 real origination-reference
  commits and admitted the id), `generate_registry`/`generate_mapping`/
  `write_registry_and_mapping`, `generate_artifacts_declarations(...,
  work_item_type="product")`, `publish_plan_revision`, and step 3's
  staging step — and `prepare-ai-review.sh <base> plan milestone-9`
  exited 0 with a valid bundle: `work_item_type: product`,
  `plan_revision: 1`, both identifiers write→recompute→equal. Its
  generated implementation-stage classification then resolves a real
  product change correctly: `app/` source and tests and
  `gradle/libs.versions.toml` `protected`; `scripts/`,
  `docs/ai-workflow/WORKFLOW_V2_PLAN.md` and `docs/ACTIVE_MILESTONE.md`
  `excluded`; its own declarations file `protected`. Baseline
  `v2.3.0` refused this work item outright at
  `resolve_plan_stage_metadata`'s type gate; after the `workflow-v2-3-1`
  hotfix it reached the type gate and failed at the generated
  declarations instead (`B4`/`B5`), and without a staging step it could
  not have generated a bundle at all (`B6`).

- **Real-history replay of the live wedge.** A disposable clone at
  `79afd07` (the `workflow-v2-3-1` wedge tip), with the previous round's
  real `generation_head` `09e662d` and `implementation_revision: 2`
  restored into `MANIFEST.md`:
  - the script as shipped at that commit exits 1 with
    `new head '79afd07…' (previous bundle was '09e662d…') requires
    implementation_revision to advance by exactly one, from 2 to 3, got 2
    (GPT-R43-001)` — the campaign's own reported symptom, reproduced
    verbatim;
  - the repaired tooling exits 0 and publishes, with
    `reviewed_implementation_head: 50a3bb1…` (the state's own value) and
    `generation_head: 79afd07…` (the commit the digest was measured at)
    now naming two different commits;
  - the durable state is untouched (`phase`
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, revision 2,
    `reviewed_implementation_head` 50a3bb1);
  - `verify_implementation_provenance_interval` returns `79afd07`,
    `implementation_provenance_interval_reachable` is `True`, and
    `assert_local_generation_matches` passes — i.e. the round is
    approvable, which is the state the live item could never reach.

## 7. Audit passes, and what each found

The campaign ran five audit passes. Each was a fresh sweep, and the
repairs from the previous pass were themselves in scope.

| Pass | Focus | Found |
|---|---|---|
| 1 | Phase A-E: model reconstruction, lifecycle, identity semantics, command consistency, test quality | `B1`-`B5`, `I1`-`I5`, `O1`-`O4`, `D1`-`D2` |
| 2 | First clean-slate: legacy path, remaining commands, mechanical symbol/keyword sweep, fixture fidelity | `B6`, `I6`, `O5`, `O6`, `D3` |
| 3 | Concurrency and mixed chains; two typed work items in one repository | `B7`, `O7` |
| 4 | Remediation child's own cycle; functional-round evidence | `I7` |
| 5 | Guard/lease machinery, malformed records, `REJECTED` refusal, review-command write paths, adversarial re-read of the campaign's own diff | `O8` only — **zero** new BLOCKING, **zero** new IMPORTANT |
| 6 | Confirmation sweep after `O8`: model re-derivation, the mechanical command↔Python symbol/keyword check, a sweep for any remaining hardcoded work-item literal, state-writer conformance, and the two real-history replays | **nothing** — the one remaining literal (`LEDGER_COVERAGE_WORK_ITEM_ID`) is a documented, deliberately item-specific constant of `WFO-LEDGER-COVERAGE` |
| 7 | **Independent post-repair review** of `cac1abe` (external, `REVISE`: 1 Blocking, 2 Important, 6 Optional), each Blocking/Important finding independently reproduced before any repair | `B8`, `I8`, `I9`, `O9`, `O10`, and `I10` (found *by* removing `O9`'s forgery) |
| 8 | **Bounded dead-contract cleanup**: the one decision pass 7 left open. Dependency closure of `/accept-scoped-remediation`, then retirement | nothing new — `I10` closed by retirement, and its own pass-7 severity and test-count bookkeeping corrected |

Passes 1-6 found strictly fewer defects each time, and the two found in
passes 3 and 4 were both surfaced by *raising the acceptance matrix's
fidelity* rather than by reading more code — the pattern ledger row `I5`
predicts.

Pass 7 is different in kind: it is an **independent** review, not another
self-audit pass, and it found a Blocking defect after pass 6 reported a
clean sweep. That is worth recording plainly rather than smoothing over.
The reason pass 6 missed `B8` is instructive and is now a ledger cluster
in its own right (`C10`): every mechanical check this campaign runs
verifies that a *named symbol exists* — the command↔Python symbol/keyword
check, the state-writer census, the phase-assignment census — and `B8` is
a command naming **no** symbol where one was required. A census of what is
named cannot see an omission. The acceptance matrix is the mechanism that
would have caught it, and the matrix's own `C5` row was forging the
missing transition by hand (`O9`), which is ledger row `I5`'s pattern —
*a fixture that encodes the defect as its specification* — recurring one
campaign later. Pass 7's fourth fidelity rule (no row may hand-write a
`phase` value) closes that specific recurrence.

The mechanical checks re-run in pass 6, at that pass's own final `HEAD`:
all 102 distinct `workflow_state`/`workflow_fingerprint` symbols the
command files name exist and every keyword argument they pass is real; the
state-writer census resolves 20 surface files (13 writers, 1 publisher,
6 non-writers) and `WFO-STATE-SERIALIZATION` returns `PASS`; the 14
phase-assignment sites match §1.2 exactly.

Those three figures are pass-6-era counts and are left as recorded rather
than restated, since pass 8 changed two of the populations they measure:
deleting `.claude/commands/accept-scoped-remediation.md` (a
`state_writer: true` file) takes the census to **19 surface files — 12
writers, 1 publisher, 6 non-writers** — and removing
`apply_scoped_remediation_acceptance` removes one phase-assignment site
along with the symbols that command named. `WFO-STATE-SERIALIZATION` still
returns `PASS`, and §1.2/§1.3 above are updated to match; the checks
themselves are re-run by
`workflow_state_completion_obligations_test.py` and
`workflow_state_demo_test.py` at every commit, so the live counts are
asserted by execution rather than by this paragraph.

## 8. Areas still unresolved

Every candidate in `WORKFLOW_DEFECT_LEDGER.md` is `FIXED`, `ACCEPTED` (a
documented, evidence-backed finding left non-gating by an explicit
decision), or `CLOSED` (disproven with evidence). No `STRONGLY_SUSPECTED`
row remains, and **no row is carried forward as a decision a future round
still owes**.

Pass 7 left exactly one open decision here — `I10`, which pass 7 recorded
as `IMPORTANT`/`ACCEPTED`. Pass 8 closed it, and corrected the
classification while doing so:

- **`I10` — `/accept-scoped-remediation`'s entry precondition is
  unreachable through the `"2.1"` lifecycle.** `AWAITING_FUNCTIONAL_REVIEW`
  with a non-terminal own registry has no producer, because the only
  entry into that phase's component requires every checkpoint `COMPLETE`
  and the only way to grow the registry afterwards routes back through
  `apply_plan_approval` → `IMPLEMENTING`. The design (plan revision 22,
  `WF8B-002`) predates `record_bundle_generation`'s source-phase guard
  (`OPUS-R101-001`), which closed the route it was written for. Nothing
  wrong happens — the command simply refuses.

  Pass 7 offered two ways to resolve it. **Pass 8 took the second:
  retirement.** The first — giving the continued-scope entry a real writer
  — would require either weakening `record_bundle_generation`'s
  source-phase guard, which is itself a correctness fix, or adding a new
  remediation lifecycle; and the case it would serve is already covered by
  `/milestone-implement` (finish the outstanding checkpoint),
  `/apply-functional-review`'s bounded branch (same-scope functional fix)
  and its broad branch (a remediation child work item). The command, its
  gate, its evidence guard, its confirmation parser, its replay classifier
  and its acceptance writer are deleted; the shared primitives the
  supported paths reach are kept; and every operator-facing message that
  pointed at it now names those three paths instead. Its severity is
  corrected to `OPTIONAL` — a dead contract is a maintenance and
  operator-confusion cost, not a state-integrity hole — which also removes
  the contradiction between this ledger row and §11.6's "zero new
  Important findings". Status: `FIXED`.

---

## 9. Disposition of this branch's in-flight `workflow-v2-3-1` state

Required by the pass-7 review's `I-1`, and recorded here rather than
resolved by editing state.

**The facts, recomputed at this branch's HEAD:**

```
work_items['workflow-v2-3-1'].plan_approval
  approved_review_content_id : 1597efbcd89bf10477776e039d87a1c73fb2a53e...
  recomputed (live)          : 25c3a5463bf08de53e50d0b21c40fce38a72eee2...
  stored status              : "CURRENT"
  approval_is_current(...)   : False
  implementing_entry_reachable(...) : False
  reviewed content manifest  : identical, all three protected blobs unchanged
```

The digest moved because commit `a2ac9a9` added
`docs/ai-workflow/audit/` to that item's
`plan_stage.excluded_prefixes` — a classification change, which the
plan-stage projection hashes (ledger `I9`). No protected file's bytes
changed.

**The disposition:**

1. **The item is stale, and is not claimed `CURRENT`.** The persisted
   `status` field still reads `"CURRENT"` because it is a cache written
   at approval time and nothing re-writes it; the authority is
   recomputation, and recomputation says stale. Every gate that governs
   forward motion agrees: `approval_is_current` and
   `implementing_entry_reachable` both return `False`, so
   `/milestone-implement` step 1a refuses this item today.
2. **No approval history was fabricated and no state byte was
   hand-edited.** The branch is not "cleaned". There is no sanctioned
   writer that marks a plan approval stale (unlike
   `mark_technical_approval_stale` at the implementation stage), and
   inventing one, or poking `status`, would be exactly the
   fabricate-to-tidy move the review forbids — and would also be a
   Workflow v2.x governance act on a branch whose governance note says
   this campaign does not use Workflow v2.x to govern itself.
3. **The item is historical reproduction and runtime evidence.** It is
   what acceptance-matrix row `G1` structurally replays and what ledger
   `B1`/`I1`'s real-history replay (§6) was run against. It stays exactly
   as it is, for that purpose.
4. **What resuming it would require**, if anyone ever does: the
   procedure in `REVIEW_PROTOCOL.md`'s "Repairing an artifact declaration
   after an approval" — a fresh `plan_revision`, the two-stage plan
   review, and `/approve-review plan`. Not a status edit, and not a
   widened exclusion to make the classification change disappear.
5. **This round changed none of it.** Pass 7 edited the declaration
   *generator*, never any declaration file on disk, so no live work
   item's identity moved. Verified by re-running both demo suites, which
   read this repository's own real state and registries:
   `workflow_state_demo_test.py` 46 OK, `workflow_fingerprint_demo_test.py`
   15 OK.

## 10. Convergence pass 7 — verification performed

- **Reproduction before repair**, for each Blocking/Important finding,
  recorded per row in `WORKFLOW_DEFECT_LEDGER.md`: `B8` through the real
  fixture and the real entry points; `I8` through the real generator with
  a measured per-path classification table at both stages; `I9` against
  this repository's own live state and the commit that caused it.
- **Three isolated control arms**, each the repaired sources with exactly
  one repair's hunks reverted, so each repair's binding failure set is
  measured rather than inferred — the table is in
  `WORKFLOW_ACCEPTANCE_MATRIX.md`'s execution record. `B8`'s arm fails
  exactly `B14` and `C5`; `I8`'s fails exactly `F3`, on exactly the eight
  paths it names.
- **Every Workflow Python suite, both demo suites, the acceptance matrix
  and the Android gate**, all green — numbers in §6 above.
- **Four targeted disposable-repo replays**, each driving the real
  `prepare-ai-review.sh`:

  | # | Replay | Result |
  |---|---|---|
  | 1 | plan re-approval with every checkpoint already `COMPLETE` | `IMPLEMENTING` → wrap-up → `SELF_REVIEWING_IMPLEMENTATION` → `same_content` republication (rc 0, revision pinned at 1) → technical approval → `MILESTONE_COMPLETE` |
  | 2 | product milestone touching `docs/UX_FLOWS.md` + root build files | first implementation bundle rc 0; reviewed manifest = `app/…/Feature.kt`, `build.gradle.kts`, `docs/DOMAIN_GLOSSARY.md`, `docs/UX_FLOWS.md`, `docs/adr/0004-new.md`, own declarations file; `README.md` correctly absent → `MILESTONE_COMPLETE` |
  | 3 | declaration repair changing plan-stage classification | digest changes while the content manifest stays identical; stored `status` `CURRENT` vs `approval_is_current` `False`; `implementing_entry_reachable` `False`; fresh revision + two-stage review + approval restores `CURRENT`; bundle rc 0 |
  | 4 | ordinary / `same_content` / recovered in one chain | ordinary rc 0 rev 1; `same_content` rc 0 rev 1, head pinned; recovered rc 0 rev 1, head pinned, supersession continuous → `MILESTONE_COMPLETE` |

- **Focused post-fix re-audit** of the areas this round touched and their
  immediate lifecycle neighbours — §11.

## 11. Convergence pass 7 — focused post-fix re-audit

A fresh sweep of exactly what this round touched, plus each area's
immediate lifecycle neighbours. Scoped deliberately: this is not a
seventh full-system pass, which the round's own bounds exclude.

### 11.1 Mechanical checks, re-run at final `HEAD`

| Check | Before (`cac1abe`) | After | Verdict |
|---|---|---|---|
| distinct `workflow_state`/`workflow_fingerprint` symbols named by `.claude/commands/*.md` | 99 | 100 | `enter_self_reviewing_implementation` added, **none removed**, none missing from its module; its four-parameter signature matches what the two commands pass |
| state-writer census (`discover_state_writers` at `HEAD`) | 13 writers / 1 publisher / 6 non-writers | identical | the new writer lives in an already-declared writer file, so the surface is unchanged |
| `verify_wfo_state_serialization` | `PASS` | `PASS` — "13 writer(s), 1 publisher, 6 non-writer(s) all conform" | unchanged |
| `phase`-assignment sites (AST) | 14 | 15 | the one addition is the second `SELF_REVIEWING_IMPLEMENTATION` writer; the census matches §1.2 exactly |

### 11.2 Adversarial re-read of the new writer

| Probe | Result |
|---|---|
| empty registry (`checkpoints: []`) | transitions — vacuously terminal, matching `complete_checkpoint`'s own `all([]) == True`; the two writers cannot disagree |
| a `current_checkpoint_id` that is `IN_PROGRESS` | refused, naming it — `select_next_checkpoint`'s rule 1 flows through `registry_completion_status` correctly |
| a registry entry the work item never recorded | refused, naming it |
| a checkpoint blocked on an unmet dependency (rule 4) | refused with the blocked id, not a leaked `NoCheckpointReadyError` |
| unknown `work_item_id` | `KeyError`, exactly as every sibling writer in this module |
| no-op path | returns the **input object**; `state_revision` and `last_transition` untouched |
| `MILESTONE_COMPLETE`, `LEGACY_READY`, `PLANNING`, `AWAITING_FUNCTIONAL_REVIEW`, and every other phase | all refused with `IllegalSelfReviewEntryPhaseError` naming the actual phase and the legal source |

### 11.3 Structural invariants of the repaired template, both types

Checked over the generated output for a `product` and a `process` item:
`protected_paths ∩ excluded_paths` empty; `protected_prefixes ∩
excluded_prefixes` empty; every prefix key ends in `/`; the item's own
declarations file still classifies `protected`; the item's own
plan/registry/mapping paths all classify implementation-stage `excluded`;
plan-stage `protected ∩ excluded` empty; and every entry in all six
mappings carries a non-empty justification.

### 11.4 One ordering property, recorded rather than repaired

A product work item whose `plan_path` were hand-placed **inside** a
product deliverable prefix (e.g. `docs/adr/0009-plan.md`) classifies
implementation-stage `protected` rather than `excluded`, because
`classify_path_implementation_stage` checks `protected_prefixes` before
`excluded_paths`. Recorded, not repaired, because:

- it is unreachable through the sanctioned generators — `/milestone-plan`
  puts a product plan under `docs/milestones/`, which is a
  `_WORKFLOW_MACHINERY_PREFIXES` entry;
- the same shape already existed for a `process` item with a `plan_path`
  under `scripts/`, and predates this round;
- it fails **closed**: the effect is that plan revisions during
  implementation stale `technical_approval` more eagerly, never less;
- before this round the same path was *unclassified* for a product item,
  i.e. a hard refusal — so the new behaviour is strictly better than what
  it replaced.

Changing `classify_path_implementation_stage`'s check order is a
frozen-classifier change (`OPUS-R20-003`) and is out of this round's
bounds.

### 11.5 Command-file dual-mode consistency

`/milestone-implement` step 2 now performs a state write, so step 0's
`"2.1"` branch description — which said "steps 2-5 execute unchanged" —
was itself made accurate rather than left as prose that the body
contradicts, which is precisely cluster `C10`'s failure mode. The new
write is `[2.1]`-marked inline, so the `"1"` branch stays v1-inert, and
`workflow_integration_test.py`'s dual-mode conformance and golden-hash
checks both pass at final `HEAD`.

### 11.6 Result

**Zero new Blocking findings. Zero new Important findings.** One
pre-existing reachability finding was surfaced *by* the repairs and is
recorded as ledger `I10` — first written up as `IMPORTANT`/`ACCEPTED`,
which contradicted this very sentence, and corrected in pass 8 to
`OPTIONAL`/`FIXED` when the dead command was retired (see §8 and §12) —
and one ordering property is recorded in §11.4 as an observation rather
than a defect.

---

## 12. Convergence pass 8 — retiring the one dead contract

Pass 7 closed with exactly one open decision (§8): `/accept-scoped-remediation`
was documented as part of the supported operator contract, and its own
entry precondition was unreachable through every supported lifecycle. Pass
8 is bounded to resolving that, and to correcting the bookkeeping around
it. It reopened no salvage work, added no lifecycle, and changed no
behaviour of the bounded functional-fix or remediation-child paths.

### 12.1 Dependency closure, confirmed independently

Before removing anything, the full closure was re-derived from a
repository-wide sweep, and each item classified as *dead-only* or
*shared*:

| Surface | Dead-only (removed) | Shared (kept) |
|---|---|---|
| Command file | `.claude/commands/accept-scoped-remediation.md` | — |
| Gate | `scoped_remediation_gate_reachable` | `milestone_complete_gate_reachable`, `registry_completion_status`, `resolve_own_registry_completion_status` |
| Evidence | `build_scoped_remediation_live_snapshot`, `verify_functional_checklist_evidence` | `discover_functional_checklist_commits`, `discover_current_functional_checklist_evidence`, `FUNCTIONAL_CHECKLIST_PATH` |
| Trailer discovery | `discover_scoped_remediation_commits` | `_discover_trailer_commits`, `_commit_trailers` |
| Confirmation | `parse_scoped_remediation_confirmation_binding_fields`, `APPROVAL_STAGES`' `"scoped_remediation"` member | `validate_user_confirmation`, the three surviving stage keywords |
| Round records | `resolve_scoped_remediation_round`, `build_scoped_remediation_live_fields`, `apply_scoped_remediation_acceptance`, `SCOPED_REMEDIATION_ACCEPTANCE_FIELDS`, `_SCOPED_REMEDIATION_COMPARISON_FIELD_MAP`, `NoExistingRound`/`ExactReplay`/`ConflictingDuplicate`/`MalformedAcceptanceRecord`/`AmbiguousHistory` | — |
| Exceptions | `AmbiguousScopedRemediationTrailerError`, `MissingFunctionalChecklistEvidenceError`, `StaleFunctionalChecklistConfirmationError`, `MalformedFunctionalChecklistEvidenceError`, `DirtyFunctionalChecklistPathError`, `ScopedRemediationLiveValueChangedError` | `AmbiguousFunctionalChecklistTrailerError`, `NonFirstParentFunctionalChecklistEvidenceError`, `IncompleteOwnCheckpointsError` |
| Rosters | the command's entry in `REVIEW_SUBJECT_ROSTER` (15 → 14 files, 11 consumers / 3 exempt) | the roster mechanism itself |
| Tests | `TestScopedRemediationEndToEnd` (11), `TestResolveScopedRemediationRound` (12), `TestFunctionalChecklistEvidenceGuard` (9), `TestApplyScopedRemediationAcceptance` (5), `TestScopedRemediationConfirmationParsing` (5), one ambiguous-trailer test — 43 in total | `TestFunctionalChecklistTrailerDiscovery` (9 remaining), `TestRegistryCompletionStatusAndGates`, `TestCompleteWorkItemOwnRegistryGuard` |
| Evidence bindings | — | `workflow-v2-1-core-wf8c-evidence.json`'s 37 entries for items 171-218, re-pointed rather than dropped (below) |

The one judgment call worth naming: `verify_functional_checklist_evidence`
sat in the middle. `/prepare-functional-review` **produces** the evidence
it checked and `/review-functional` **reads** that evidence, so both the
production and the lookup are shared and stay. The guard itself only ever
bound a *user confirmation* to that evidence, and only the retired command
ever supplied such a confirmation — so it is dead-only and goes. The two
properties it proved for ledger `I7`'s `--allow-empty` branch (the
commit's committed content equals the blob its trailer names; the
checklist path is clean) are now asserted directly in acceptance-matrix
`C8`.

**The tail the first sweep missed, and how it was caught.** Deleting those
43 tests broke something no grep for "scoped" would have surfaced:
`workflow-v2-1-core`'s completion obligation `WFO-LEDGER-COVERAGE` requires
every non-`SUPERSEDED` requirement-ledger item to name a test id that
still *resolves and executes green at the verified commit*, and 37 items
(171-218) named one of the deleted tests. `workflow_state_demo_test.py`
caught it on the first post-commit run — that suite reads `HEAD`, not the
worktree, so it could only fail after the change was committed. Recorded
plainly rather than smoothed over: sweeping the sources for a symbol is
not equivalent to running a `HEAD`-reading conformance obligation.

The 37 entries in
`docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json` are
**re-pointed**, to
`workflow_integration_test.TestRetiredScopedRemediationLeavesNoLiveSurface`,
with each note quoting the test id it named before. That is the honest
binding: the requirement's subject no longer exists, so what discharges it
now is the proof that the retirement is complete, not a proof that retired
behaviour still works. Deliberately *not* done: relabelling those items
`SUPERSEDED`/`none`, which is the verifier's own exemption. That would
require editing `WORKFLOW_V2_PLAN.md`'s approved reconciliation table
(properties (iii)/(iv) bind ledger status and owner to it), and those
requirements genuinely *were* `IMPLEMENTED` under `WF8b` at the time. The
plan table and `ledger-status.json` are unchanged; only the evidence
companion — an explicitly living, incrementally-populated artifact — moved.

### 12.2 What replaced the guidance

Every operator-facing pointer at the retired command was replaced, not
deleted: `IncompleteOwnCheckpointsError`'s message, `/accept-milestone`
step 2a's two refusal branches, `/prepare-functional-review` steps 3a and
4, `/review-functional`, `MILESTONE_WORKFLOW.md`'s
`AWAITING_FUNCTIONAL_REVIEW`, `MILESTONE_COMPLETE` and hard-gate sections,
`REVIEW_PROTOCOL.md`, `WORKFLOW_V2_1_OPERATOR_REFERENCE.md` and
`CLAUDE.md` all now name the same three supported paths: finish the
outstanding checkpoint with `/milestone-implement`;
`/apply-functional-review`'s bounded branch for a same-scope functional
fix; its broad branch — a `<parent-id>-remediation-<n>` child work item —
for new or wider scope.

The executable surface and the documentation are held to different rules,
deliberately. No `.claude/commands/*.md` file names the retired command or
its stage keyword **at all** — a command file is a script an agent
executes step by step, and a retirement footnote there is noise at best.
The operator docs *may* name it, but only inside a paragraph that says it
was retired, so someone who remembers the command finds out why it is gone
instead of concluding the docs are stale.
`workflow_integration_test.py`'s
`TestRetiredScopedRemediationLeavesNoLiveSurface` asserts both rules.

### 12.3 What was deliberately left alone

- **The historical record.** `docs/ai-workflow/WORKFLOW_V2_PLAN.md`
  (`D-Scoped-Remediation-Acceptance`, plan revisions 22-27 and their six
  external review rounds), `docs/ai-workflow/requirements/*-ledger.md` and
  `*-mapping.json`,
  `docs/ai-workflow/registry/workflow-v2-1-core-ledger-status.json`,
  `docs/ai-workflow/dry-run/WF8B_*`, and
  `WORKFLOW_STATE.json`'s `workflow-v2-1-core` technical-approval manifest
  all still name the command and its helpers. They record what was
  designed, reviewed and approved at the time; rewriting them would
  falsify provenance, and several are plan-approval-protected paths whose
  bytes a recorded `review_content_manifest` pins. None is a surface an
  operator is routed through. The one registry file that *did* change is
  the evidence companion, `workflow-v2-1-core-wf8c-evidence.json` — see
  §12.1's tail; its re-pointed notes quote every prior binding verbatim,
  so nothing is lost there either.
- **`record_bundle_generation`'s source-phase guard** (`OPUS-R101-001`),
  the guard that closed the route the retired design was written for. It
  is a correctness fix; retirement was chosen precisely so it would not
  have to be weakened.
- **Bounded functional-fix and remediation-child semantics.** Only
  documentation and cross-references changed; `C8` and `C6` pass unchanged.

### 12.4 Bookkeeping corrected while in these documents

- `I10`'s severity: `IMPORTANT` → `OPTIONAL`, and status `ACCEPTED` →
  `FIXED`. The old pairing made the ledger contradict §11.6's "zero new
  Important findings" in the same pass that wrote both.
- `TestScopedRemediationEndToEnd`'s size: the ledger and the acceptance
  matrix both said **13 tests**; the class held **11**. Corrected in both,
  for the record, even though the class no longer exists.
- `WORKFLOW_V2_1_OPERATOR_REFERENCE.md`'s command count: it claimed to
  cover "all 14 commands in `.claude/commands/`". Removing the retired
  command's section leaves 13 — but the sentence also carried an older,
  unrelated inaccuracy, since `workflow-v2-3`'s `/review-implementation`
  and `/review-functional` were never sectioned there and the directory
  has held more files than the reference documents since they landed. The
  sentence at the time stated both real numbers (13 sectioned, 15 on disk)
  and said where the other two were documented, recording — not closing —
  the gap, since writing two new operator sections was outside pass 8's
  bounds. **Update, documentation-only synchronization pass (2026-08-26):
  that gap is now closed.** `WORKFLOW_V2_1_OPERATOR_REFERENCE.md` sections
  all 15 live commands, including `/review-implementation` and
  `/review-functional`, and its command count is asserted against
  `.claude/commands/*.md` by `workflow_integration_test.py`'s
  `test_the_operator_reference_command_count_matches_reality` rather than
  hand-maintained. **Further correction (convergence pass 9, ledger
  `O15`):** that last clause overstated the guarantee, and the reference
  repeated it. What is derived is the **set equality** between the
  reference's `### /<name>` sections and the on-disk stems. The number 15
  itself is a hardcoded literal in the same test
  (`assertEqual(len(on_disk), 15)`) — a deliberate tripwire so a roster
  change is a reviewed edit, not a hand-maintained derivation. Both the
  reference and this paragraph now say so.

### 12.5 Result

Zero new findings. The ledger's last open decision is closed, every row is
now `FIXED`, `CLOSED` or a deliberately `ACCEPTED` non-gating observation,
and no claim in this audit contradicts the ledger. Every Workflow Python
suite, both demo suites, the acceptance matrix and the Android gate are
green at pass 8's `HEAD` — numbers in §6.

---

## 13. Convergence pass 9 — second independent review, then a systematic re-audit

A second independent Opus review of `b0b5ba2` returned nine findings
against the operator reference (`A1`-`A9`), six against the lifecycle
diagram (`B1`-`B6`) and six against its rendering (`C1`-`C6`). Every one
was independently reproduced before anything changed; the re-audit that
followed found four more.

### 13.1 What the review found, and what it turned out to be

`A9` was the only one that was not a documentation defect. It was reported
as stale prose — the broad functional-remediation branch telling an
operator to run `/milestone-plan <child-id>` against a `[base-sha]`-only
contract — and reproducing it showed a **structural** gap: four commands
on a remediation child's own lifecycle could not be pointed at a work item
that is not `active_work_item_id`, which a child never is. The child could
not be planned or accepted, and a parent whose child cannot complete can
never complete either. Recorded as ledger `B9`, repaired as `R16`.

Everything else was a claim about the system that did not match the
system: see ledger rows `I11`-`I15` and `O15`-`O18`. Two of them
(`I11`, `I13`) had reproducible operational consequences — following the
documented happy path invalidated an external `APPROVE` in hand, and the
release gate's own refusal was documented nowhere.

### 13.2 Phase-writer census, re-derived twice

Two independent derivations, deliberately by different techniques so a
mistake in one does not hide in the other: an AST walk of every `phase`
assignment in `scripts/workflow_state.py`
(`workflow_state_test.TestPersistedPhaseWriterCensus`) and a line scan of
the same module (`workflow_integration_test._phase_writing_state_functions`).
They agree, and they agree with §1.2's table: 13 of the 17 declared phases
are written, by 14 writer functions. The four with no writer are
`SELF_REVIEWING_PLAN`, `AWAITING_TECHNICAL_APPROVAL`,
`FIXING_FUNCTIONAL_FINDINGS` and `AWAITING_USER_ACCEPTANCE`.

The census is now the thing the operator reference's own phase tables and
the diagram's phase boxes are checked against, in both directions: a new
writer forces both documents to move, and a phase drawn as a live box must
be one the code writes.

One asymmetry the re-audit surfaced and both documents had flattened:
`AWAITING_PLAN_APPROVAL` is a persisted phase for a `"2.1"` item and a
gate name only for a `"1"` one, because its single writer
(`record_manual_plan_review`) refuses any item that is not `"2.1"`-governed
(`O20`).

### 13.3 Command inventory, re-derived

All 15 command files were re-read. Every `argument-hint` now equals the
reference's own `### /<name> <args>` heading (asserted), every command that
names a phase-writing helper declares a `[work-item-id]` argument
(asserted, with `/bootstrap-workflow-v2` the single exemption, which its
own file explains), and every `**Expects**` line's phase/status names were
cross-checked against the command file that owns them — which is what
found `O19`.

### 13.4 Test fidelity, attacked rather than run

- Acceptance-matrix row `C6` hand-wrote `phase: "MILESTONE_COMPLETE"` onto
  the remediation child to clear the parent's block. That forged
  transition skipped planning, approval, implementation and the child's own
  completion obligations — and would have stayed green even though no
  command could select the child at all. Removed; row `C7` now owns the
  child's real completion, and gained a plan-review `REVISE` round driven
  while the parent still holds the pointer, an assertion that the parent
  stays blocked right up to `/accept-milestone <child-id>`, and a
  post-approval registry tamper that must be refused.
- The two remaining hand-written `phase` assignments in the matrix
  (`E7`, `E10`) were re-read and kept: both are deliberate forgeries in
  *negative* tests, proving a guard refuses tampered state.
- **Ten mutation/control-arm checks** were run against the guards this
  campaign depends on. All ten were caught, each by a focused, correctly
  named test: `record_bundle_generation`'s source-phase guard and its
  `STALE` requirement, `enter_applying_review_feedback`'s phase guard,
  `complete_work_item`'s obligation gate and incomplete-child block,
  `_require_v2_1_plan_review`'s version guard,
  `technical_approval_gate_reachable`'s durable `BLOCK` pin,
  `publish_plan_revision`'s version branch, `route_work_item`'s
  focus-stealing guard, and
  `create_remediation_child_work_item`'s governing-version fixing.

### 13.5 The diagram

Rendered at several zoom levels and inspected, not just parsed. The
rendering defects were real and worse than reported: the `LEGACY_READY`
note did not merely overlap a live box, it covered the
`▶ IMPLEMENTING — continues in lane 2` box completely, along with the
arrow into it.

The file carries the diagram twice — the drawio `mxfile` in the
`<svg content="...">` attribute and hand-authored SVG primitives in the
body — and nothing kept the two in agreement. Sixteen tests now do:
four for model-versus-body agreement in both directions plus no
duplicate edges, five for geometry (no box overlap, no edge under a box,
no label over a box, no undocumented crossing, nothing off-canvas), and
seven checking the diagram's own text against the functions it describes.

Two edge crossings remain, deliberately and by name: three edges leave
lane 2 leftwards at interleaved heights, so a crossing is topologically
forced. Both survivors are between a solid grey edge and a dashed red one.
A red-on-red crossing that the *first* repair pass introduced was found by
the geometric audit and rerouted away — which is the case for having the
audit at all.

### 13.6 Result

`B9` (Blocking) repaired with runtime evidence; `I11`-`I15` (Important)
repaired; `O15`-`O20` (Optional) repaired except `O16` and `O17`, both
recorded `ACCEPTED` with why they are inert and why closing them belongs
outside a convergence pass. Every Workflow Python suite, both demo suites,
the acceptance matrix and the Android gate are green at this pass's
`HEAD`.

---

## 14. Convergence pass 10 — third independent review, then its own re-audit

### 14.1 What the review found

`REVISE`: 0 Blocking, 2 Important, 2 Optional, against `fce361f`. Both
Important findings were about the same function and both were real:
`resolve_bundle_dir` answered the flat `.ai-review/current/` for a work
item's first plan bundle while the generator wrote and validated the
scoped directory (`I16`), and the acceptance matrix's own `Item.bundle_dir`
hardcoded and pre-created the scoped path, so no row could observe the
resolver (`I17`). Both were reproduced before anything changed — `I16` in
all four scenarios the review named, each failing its first attempt and
succeeding on an identical second one; `I17` by the control arm that
restores the hardcoded helper against a *correct* resolver and finds the
matrix still green.

Of the two Optional findings, one (`O24`, the takeover target check) was
reproduced by taking over work item `proc-item`'s open transaction while
targeting `milestone-9` — it succeeded — and repaired. The other (`O25`,
the withdrawal crash window) was offered an "accept if cosmetic and all
consumers fail closed" escape which did not hold: simulating the exact
window showed `assert_bundle_not_rejected` passing over a live scoped
marker, i.e. failing **open**. It is repaired, but by no machinery of its
own: it falls out of `I18`'s change to the layout rule the two resolvers
already share.

### 14.2 The re-audit, and what it turned up

The closure re-read was `resolve_bundle_dir`, `resolve_feedback_dir`,
bundle authoring, `prepare-ai-review.sh`, `withdraw_bundle`, regeneration,
stale/recovered provenance, both stages' generation paths, and the
matrix's own `Item` helpers.

It found one further Important issue, `I18`, of exactly the class the
re-audit was asked to look for. `withdraw_bundle` renames `current/` away,
so the existence gate reported "this item is on the flat layout" for an
item that had been generating scoped bundles for rounds — and the
implementation/post-fix round after **any** withdrawal authored flat while
the generator, given the same `[work_item_id]`, wrote scoped, withdrawing
the regenerated bundle too. `stage="plan"` cannot fix that one: the flat
layout is genuinely reachable at those stages. What is not a legitimate
signal is `current/`'s existence, since a withdrawal is defined to remove
it, so both resolvers now key on the work item's own root directory.

Also checked, and **not** repaired: `workflow_fingerprint_generalization_test.py`
hardcodes scoped bundle paths in its own plan-stage generation tests. Those
tests assert on what `prepare-ai-review.sh` itself writes — a path the
script templates directly — rather than on where a *command* authors, so a
literal there is testing a fixed literal path, which the test-fidelity rule
explicitly permits. The suite that made the false command-level claim is
the acceptance matrix, and it is repaired.

### 14.3 What was deliberately not done

- The flat compatibility layout is **not** deleted. It is a reachable
  generation target for three of the four stages, by documented design;
  proving it obsolete was not attempted and is not claimed.
- `resolve_feedback_dir` is untouched, at every stage — `feedback/` is
  stage-agnostic by contract (`REVIEW_PROTOCOL.md`, `REQ-21`), and
  changing it would silently move an already-live read path.
- Takeover semantics are not broadened: `O24` adds one refusal on a
  required argument and changes nothing else about the transaction.
- No transaction machinery was added for `O25`'s crash window.

### 14.4 Result

`I16`-`I18` (Important) repaired with runtime evidence and first-attempt
regressions for all four scenarios the review named; `O24`/`O25`
(Optional) repaired. Six mutation control arms confirm each repair is
caught by the suites that claim it, including the arm that *is* `I17`.
Every Workflow Python suite, both demo suites, the acceptance matrix, the
completion-obligations suite and the Android gate are green at this pass's
`HEAD`.

---

## 15. Convergence pass 11 — fourth independent review

Raised against `6431b70`: `REVISE`, 0 Blocking, 1 Important, 3 Optional.
The pass-10 bundle-resolver repair was independently validated. The one
remaining Important finding was a pre-existing command-contract gap in
`/apply-functional-review`'s bounded same-scope fix path.

### 15.1 The Important finding, reproduced before anything was changed

`/apply-functional-review`'s bounded-code-change branch drives a
`post-fix` bundle generation and named none of the author-written
preconditions that generation hard-requires: no `<bundle_dir>` (the
preamble resolved `<feedback_dir>` alone), no `REVIEW_REQUEST.md`
`review_content_id`, no `IMPLEMENTATION_SUMMARY.md`
`implementation_revision`.

Driven through the real bounded-fix semantics, the real
`resolve_bundle_dir`, the real `scripts/prepare-ai-review.sh`, the real
state machine and a disposable scratch repository, following the command
text exactly:

| Attempt | Result |
|---|---|
| 1 (as written) | exit 1, `ReviewContentIdMismatchError` from `assert_review_request_states_review_content_id`, raised inside `--write-manifest`. A **refusal**: `current/` intact, nothing published, nothing withdrawn |
| 2 (only `review_content_id` corrected) | exit 1, `status: withdrawn` — `assert_stage_completeness` from `finalize_bundle_generation`. `current/` quarantined to `current.rejected-<token>/`, archive deleted |
| 3 (every input re-authored) | exit 0, published |

The `same_content` control arm publishes on the **first** attempt with
nothing re-authored, because that outcome pins `implementation_revision`
and leaves the protected content byte-identical, so the previous round's
digest recomputes unchanged. That asymmetry is why the gap survived: row
`C3` was already exercising the branch that needs no refresh.

### 15.2 The census was the root cause, not a symptom

`R5` (campaign 1) corrected "the four commands that drive a generation".
Eight do. Enumerating the command corpus mechanically:

| Command | Runs the generator | Contract |
|---|---|---|
| `/milestone-plan` | `plan` | complete (`R5`) |
| `/apply-plan-review` | `plan` | complete (`R5`) |
| `/milestone-implement` | `implementation` | complete — revision line explicit, `REVIEW_REQUEST.md` per `REVIEW_PROTOCOL.md` |
| `/apply-implementation-review` | `post-fix` | complete (`R5`) — both lines explicit |
| `/apply-functional-review` | `post-fix` | **was incomplete** — ledger `I19` |
| `/recover-implementation-provenance` | the stage `MANIFEST.md` records | complete: the round is pinned by contract, verified by driving step 6 with the previous round's inputs untouched (publishes) |
| `/prepare-review` | the caller's `<stage>` | complete by reference to `REVIEW_PROTOCOL.md` |
| `/bootstrap-workflow-v2` | `implementation` | same reference; unreachable at `HEAD` (`O28`, accepted) |

`/approve-review` and `/review-implementation` name the script without
ever running it, and are recorded as mention-only with the reason, so the
two populations partition the corpus.

Second half of the root cause: `workflow_acceptance_matrix_test.py`'s
`Item.write_bundle_inputs` refreshed both marker lines for every
generation — including the bounded functional fix, whose operator
documentation named neither. The matrix was authoring, on the command's
behalf, what no operator following that command would have known to
author.

### 15.3 What was deliberately not done

- No Workflow audit was reopened, no lifecycle semantics redesigned, no
  Workflow v2.x command used as governance, and no RepFlow application
  code touched.
- `/milestone-implement`'s `REVIEW_REQUEST.md` instruction is **not**
  rewritten to name `review_content_id` inline. It routes authoring
  through `REVIEW_PROTOCOL.md`, which states the requirement; that is one
  of two sanctioned forms and the census records which form each driver
  uses rather than forcing them into one shape.
- `/bootstrap-workflow-v2` is not repaired or redesigned (`O28`).
- The flat compatibility layout is not broadened; `O27` adds coverage of
  what it already does.

### 15.4 Result

`I19` (Important) repaired in command text alone, with the reproduction
encoded as matrix rows `C9`/`C10` and the census mechanised so a ninth
driver cannot fall outside it. A mutation regression proves the
conformance test fails when the repair's own hunks are reverted — on disk
as well as in memory. `O26` and `O27` (Optional) closed; `O28` accepted
and retained. All nine Workflow Python suites, both demo suites, the
acceptance matrix, the completion-obligations suite and the Android gate
are green at this pass's `HEAD`.

## 16. Convergence pass 12 — fifth independent review, then its own re-audit

Raised against `5d2337e`: `REVISE`, 0 Blocking, 1 Important, 3 Optional.
The one Important finding (`N1`) was a supported-path defect in the
generator's own closing control flow. The pass then re-audited the
repaired dependency closure and, separately, audited this campaign's own
test helpers mechanically — which turned up two further Important
supported-path contract gaps the reviewer had not named.

### 16.1 The Important finding, reproduced before anything was changed

`scripts/prepare-ai-review.sh` gated its closing
`finalize_bundle_generation` call on `[[ -f "$BUNDLE_DIR/MANIFEST.md" ]]`
— a **filesystem** test — while two of its four stages write no manifest
at all (`functional-review` at either layout, and
`implementation`/`post-fix` with the work-item-id omitted). `$BUNDLE_DIR`
is reused across stages by design.

Driven through the real script against disposable scratch repositories
carrying real HEAD tooling, for a `process` item, a `product` item, and
over implementation-, post-fix- and plan-published scoped bundles alike:

| Observation | Before the fix |
|---|---|
| exit status | 1, `status: withdrawn`, `bundle_id mismatch: manifest=… ondisk=… extracted=…` |
| `.ai-review/<id>/current/` | gone — renamed to a `current.rejected-<token>/` sibling |
| `review-bundle.tar.gz` | deleted |
| `MANIFEST.md` | deleted |
| `WORKFLOW_STATE.json` | **byte-identical** (this script is not a state writer) |
| `technical_approval` | still `CURRENT`, over a bundle that no longer existed |
| `REJECTED` marker | absent — a *completed* withdrawal removes its own marker (correct by design, ledger `D1`), so `assert_bundle_not_rejected` had nothing to see |

The mismatch is structural, not incidental: `CHANGED_FILES.txt` alone
carries a `stage:` line the run has just rewritten, so the recomputed
`bundle_id` disagrees with the recorded one **by construction**. Every
such run could only ever end in withdrawal.

### 16.2 The invariant, and why the obvious alternative is wrong

Finalization is a **producer's** obligation. A run that generated an
identity must prove that identity reproduces; a run that generated none
has nothing to prove and no standing to withdraw another round's
artifact. The gate is now `$MANIFEST_WRITTEN`, set only on the far side
of a `--write-manifest` call that returned 0 — invocation-local producer
state that cannot drift, because it is set by the same `if/elif` that
performs the write.

Deleting the inherited manifest to suppress the check would be the same
filesystem-state reasoning inverted, and would additionally destroy the
published round's own identity record. It is left in place, the run says
so on `stderr`, and the residual is evidenced non-gating: every live
consumer *recomputes* `bundle_id` rather than reading the declared value
(`/approve-review` step 2, `/review-plan` step 5,
`/review-implementation` step 4), so an approval over an inherited
manifest fails closed (`O30`, matrix rows `J7`/`J8`).

### 16.3 The helper audit, and what it turned up

The pass then ran a mechanical audit no earlier pass had: for each
acceptance-matrix helper that stands in for a command, every
`workflow_state`/`workflow_fingerprint` entry point it calls was
extracted from the AST and checked against the owning command file's own
text. Five calls no owning command named:

| Helper call | Owning command | Verdict |
|---|---|---|
| `render_registry_markdown` | `/apply-plan-review` | **`I22`** — a real gap: the plan document's generated checkpoint table was left stating the previous revision's checkpoints |
| `validate_technical_approval_commit` | `/approve-review` | **`I21`** — a real gap: **zero** production call sites anywhere |
| `assert_feedback_matches_bundle` | `/approve-review` | **`O35`** — helper *stricter* than its command, making `USER_OVERRIDE` unreachable end to end |
| `compute_review_content_id_plan_stage_for_work_item` (×2) | `/milestone-plan`, `/approve-review` | discharged by reference once `O31` documented the canonical recipe |

`I21` and `I22` are ledger `I19`'s class exactly: in both, the helper
performed a step no operator following the command would have performed,
and that is precisely why every row stayed green over the gap. `I21` is
additionally `OPUS-R133-003`'s class — a documented, implemented,
unit-tested validator with no live caller — one approval stage over.

### 16.4 What was deliberately not done

- No Workflow v2.x command was used as governance for this campaign, no
  RepFlow application code was touched, and the parked redesign worktree
  was not touched.
- The `.ai-review/` resolver's existence gate is **not** changed to close
  `O32`. That gate is what makes the documented omitted-id compatibility
  form work at all, and the split it leaves is fail-closed and
  self-healing in one identical retry — evidenced by matrix row `I7`
  rather than argued.
- `/bootstrap-workflow-v2` is not revived, redesigned or deleted
  (`O33`) — only proven unreachable and fail-closed against live state,
  replacing a source comment with an executed row.
- `I21`'s check is **forward-only**, applied to the commit the invocation
  just created and never to discovered history. Two technical-approval
  commits already in this repository do not satisfy the contract
  (`9fd3c72`, `ae51770`) and history rewriting is not available; wiring
  the check into discovery would have refused real, already-approved
  rounds.
- No production check was added for `I22`'s stale table. Three of the
  four live work items already satisfy the property exactly; the fourth
  is the terminal, permanently `"1"`-governed bootstrap item. The option
  is recorded in the ledger for a later review to weigh rather than taken
  unilaterally here.
- No historical approved plan/review artifact was rewritten.

### 16.5 Result

`I20` repaired in the generator's own control flow; `I21` and `I22`
repaired in command contract text with mechanical conformance and
behavioural regressions behind both. `O29`, `O31`, `O34`, `O35`, `O36`
and `O37` closed; `O30`, `O32` and `O33` accepted with executed evidence
rather than narrative. The acceptance matrix grew from 97 to 146 tests.

Two of those Optionals came from turning this pass's own methods on
themselves. `O36`: the helper-vs-command sweep that found `I21`, `I22`
and `O35` was run once by hand, so it is now a standing conformance
property with its own control arm — a one-off audit prevents nothing.
`O37`: enumerating every author-written generation precondition after
`O31` mechanised the three already known surfaced a fourth, and the only
*prohibition* among them, which is why every earlier census missed it —
they were all looking for required lines.

Mutation control arms re-measured against this pass's final suite,
including a new arm for `I20` itself; `fail + pass + skip = 146` in every
arm. Each new conformance guard was additionally mutation-checked on its
own: reverting `/approve-review`'s post-commit paragraph fails five rows
including the standing helper audit, and planting a writer for
`AWAITING_USER_ACCEPTANCE` fails the phase-vocabulary guard. All nine
Workflow Python suites, both demo suites, the acceptance matrix, the
completion-obligations suite and the Android gate are green at this
pass's `HEAD`.
