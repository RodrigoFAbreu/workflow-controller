# Workflow v2.x — Defect Ledger

Every candidate correctness issue raised by the salvage audit, with its
proof, root cause, planned repair and current status.

**Confidence**: `PROVEN_EXECUTION` · `PROVEN_STATIC_CLOSURE` ·
`STRONGLY_SUSPECTED` · `DISPROVEN`
**Severity**: `BLOCKING` · `IMPORTANT` · `OPTIONAL`
**Scope**: `BASELINE_V2_3_0` · `V2_3_1_SPECIFIC` · `GENERIC_DESIGN`

Reproduction scripts referenced below live in
`scripts/workflow_acceptance_matrix_test.py` (durable, re-runnable) —
each row names the test that carries its pre-fix reproduction and
post-fix proof.

---

## Summary

| ID | Severity | Confidence | Status | One line |
|---|---|---|---|---|
| B1 | BLOCKING | PROVEN_EXECUTION | FIXED | `same_content` republication could never publish its bundle |
| B2 | BLOCKING | PROVEN_EXECUTION | FIXED | `/recover-implementation-provenance` could never publish its bundle |
| B3 | BLOCKING | PROVEN_EXECUTION | FIXED | functional bounded-fix `same_content` round hit the same refusal |
| B4 | BLOCKING | PROVEN_EXECUTION | FIXED | generated artifact declarations made the first implementation bundle impossible |
| B5 | IMPORTANT | PROVEN_EXECUTION | FIXED | generated plan-stage declarations left three repository documents unclassified |
| B6 | BLOCKING | PROVEN_EXECUTION | FIXED | `/milestone-plan` never stages the four files the resolver requires to be tracked |
| B7 | IMPORTANT | PROVEN_EXECUTION | FIXED | a sibling work item's plan document is unclassified for every other item |
| I1 | IMPORTANT | PROVEN_EXECUTION | FIXED | `MANIFEST.md`'s `reviewed_implementation_head` was not `WORKFLOW_STATE.json`'s |
| I2 | IMPORTANT | PROVEN_EXECUTION | FIXED | non-string `work_item_type` escaped as a raw `TypeError` on every non-plan path |
| I3 | IMPORTANT | PROVEN_EXECUTION | FIXED | plan-stage commands omitted `TEST_RESULTS.md`'s required markers → bundle withdrawn |
| I4 | IMPORTANT | PROVEN_EXECUTION | FIXED | implementation-stage commands omitted `IMPLEMENTATION_SUMMARY.md`'s marker → bundle withdrawn |
| I5 | IMPORTANT | PROVEN_STATIC_CLOSURE | FIXED | `same_content`/recovered rounds had no test through the real script; the one test covering the shape encoded `B1` as the spec |
| I6 | IMPORTANT | PROVEN_EXECUTION | FIXED | legacy adoption's freshness check defaulted to another work item's classification, fail-open |
| I7 | IMPORTANT | PROVEN_EXECUTION | FIXED | a round that needs no checklist change cannot create its own evidence commit |
| O1 | OPTIONAL | PROVEN_STATIC_CLOSURE | SUPERSEDED by `O34` | four documented phases are never written by any writer — pass 12 marks them in the document and holds the set true mechanically |
| O2 | OPTIONAL | PROVEN_STATIC_CLOSURE | ACCEPTED | `current_bundle_id`/`functional_acceptance_status` are vestigial |
| O3 | OPTIONAL | PROVEN_EXECUTION | FIXED | `write_registry_and_mapping` failed on a repository without the workflow directory layout |
| O4 | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | two commands still instructed authoring `PLAN.md`, which the generator derives |
| O5 | OPTIONAL | PROVEN_STATIC_CLOSURE | ACCEPTED | the manual plan-review stage's two hard-blocking feedback fields have no shared parser |
| O6 | OPTIONAL | PROVEN_STATIC_CLOSURE | ACCEPTED | `_discover_trailer_commits` resolves a single off-first-parent candidate |
| O7 | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | `/milestone-implement` has no phase guard, so a plan revision in flight does not stop it |
| O8 | OPTIONAL | PROVEN_EXECUTION | FIXED | the plan-approval mutation guard hardcoded one work item's id |
| D1 | — | DISPROVEN | CLOSED | a completed withdrawal removing its own `REJECTED` marker is by design |
| D2 | — | DISPROVEN | CLOSED | `/prepare-functional-review`'s hardcoded checklist path is not itself a defect |
| D3 | — | DISPROVEN | CLOSED | the `AWAITING_TECHNICAL_APPROVAL`/`AWAITING_USER_ACCEPTANCE` gates are not weakened by their phases being unwritten |

### Convergence pass 7 — independent post-repair review

Raised by an independent reviewer against `cac1abe` (`REVISE`: 1 Blocking,
2 Important, 6 Optional). Each was independently reproduced before any
implementation changed. Ids continue this ledger's own numbering; the
reviewer's own labels are given in the "Reviewer" column so the two can be
cross-read.

| ID | Reviewer | Severity | Confidence | Status | One line |
|---|---|---|---|---|---|
| B8 | B-1 | BLOCKING | PROVEN_EXECUTION | FIXED | a work item whose checkpoints are all `COMPLETE` is wedged in `IMPLEMENTING` after a plan re-approval |
| I8 | I-2 | IMPORTANT | PROVEN_EXECUTION | FIXED | the generated `product` implementation-stage template leaves a realistic product footprint unclassified |
| I9 | I-1 | IMPORTANT | PROVEN_EXECUTION | FIXED | "editing the artifacts declaration never stales the plan approval" is false, and was acted on |
| I10 | — | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | `/accept-scoped-remediation`'s entry precondition is unreachable through the `"2.1"` lifecycle — the command is retired |
| O9 | O-6 | OPTIONAL | PROVEN_EXECUTION | FIXED | acceptance-matrix `C5` hand-wrote `SELF_REVIEWING_IMPLEMENTATION`, hiding `B8` |
| O10 | O-4 | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | `workflow_state_completion_obligations_test.py` was not wired into CI |
| O11 | O-1 | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | a reverted protected interval cannot publish; diagnostic unchanged this round |
| O12 | O-2 | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | acceptance reads the cached approval `status` rather than recomputing |
| O13 | O-3 | OPTIONAL | PROVEN_STATIC_CLOSURE | ACCEPTED | `complete_work_item` has no phase guard; the command-level path is proven safe |
| O14 | O-5 | OPTIONAL | PROVEN_STATIC_CLOSURE | ACCEPTED | `/review-implementation` and external review share `REVIEW_FEEDBACK.md` |

`O11`-`O14` are the four Optional findings the review directed be left
non-gating. They are recorded `ACCEPTED`, not `CLOSED`/`DISPROVEN`: none
was disproven, each was *deliberately not repaired* in this bounded
round, and `O12` in particular gained new supporting evidence (below)
without becoming unsafe.

### Convergence pass 9 — independent post-repair review

Raised by a second independent reviewer against `b0b5ba2`, plus what the
systematic re-audit that followed those findings turned up on its own.
Every row was independently reproduced before anything was changed. Ids
continue this ledger's own numbering; the reviewer's own labels appear in
the "Reviewer" column.

| ID | Reviewer | Severity | Confidence | Status | One line |
|---|---|---|---|---|---|
| B9 | A9 | BLOCKING | PROVEN_EXECUTION | FIXED | no sanctioned command could select a remediation child, so neither it nor its parent could ever complete |
| I11 | A2 | IMPORTANT | PROVEN_EXECUTION | FIXED | the documented happy path routed an implementation-review `APPROVE` through the `REVISE` command, destroying the approval it had just earned |
| I12 | A1, A5 | IMPORTANT | PROVEN_STATIC_CLOSURE | FIXED | `AWAITING_TECHNICAL_APPROVAL` and `FIXING_FUNCTIONAL_FINDINGS` were documented as live persisted gates; nothing writes either |
| I13 | A6 | IMPORTANT | PROVEN_EXECUTION | FIXED | neither `/accept-milestone` nor the operator reference documented the completion-obligations refusal |
| O15 | A3, A4, A7, A8 | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | four false operator-reference claims: the `state_writer: false` count, reviewer-role casing, a provenance attribution, and the inventory guarantee |
| O16 | — | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | `/bootstrap-workflow-v2`'s own stated retirement condition is met but was never executed |
| O17 | — | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | `workflow-v2-1-core`'s artifact declarations no longer classify two documents written after it completed |
| I14 | B1, B2, B3, B6 | IMPORTANT | PROVEN_STATIC_CLOSURE | FIXED | the lifecycle diagram misstated four runtime contracts: a child's entry phase, a phase writer, `record_bundle_generation`'s outcome set, and implementation-review `BLOCK` |
| I15 | B4, B5 | IMPORTANT | PROVEN_STATIC_CLOSURE | FIXED | the diagram gave two of the four unwritten phases live-looking notes and omitted the other two entirely |
| O18 | C1-C6 | OPTIONAL | PROVEN_EXECUTION | FIXED | rendering: a live box painted over, a return edge hidden under the note column, a duplicated edge, two absent live transitions, label collisions, a state visually skipped |
| O19 | — | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | `/prepare-functional-review` was documented with a `technical_approval` precondition and a phase entry it does not have |
| O20 | — | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | `AWAITING_PLAN_APPROVAL` was shown as the `"1"` path's next state; nothing writes it for a `"1"` item |
| O21 | — | OPTIONAL | PROVEN_EXECUTION | FIXED | the remediation-child branch told the operator to edit a checklist the child had already overwritten |
| O22 | — | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | "Enter the `X` state" in eight command files does not mean the command writes `X`, and `REVISING_PLAN` is `"2.1"`-only |
| O23 | — | OPTIONAL | PROVEN_EXECUTION | FIXED | the operator reference named a helper that does not exist (`normalize_plan_review_stage_keys`) |

### Convergence pass 10 — third independent review, then its own re-audit

Raised by a third independent reviewer against `fce361f` (`REVISE`: 0
Blocking, 2 Important, 2 Optional), plus the one finding the re-audit that
followed turned up on its own. Every row was independently reproduced,
against the real resolver and the real `scripts/prepare-ai-review.sh`,
before anything was changed. Ids continue this ledger's own numbering;
the reviewer's own labels appear in the "Reviewer" column.

| ID | Reviewer | Severity | Confidence | Status | One line |
|---|---|---|---|---|---|
| I16 | I1 | IMPORTANT | PROVEN_EXECUTION | FIXED | a work item's first plan bundle resolved to the flat `.ai-review/current/` while the generator wrote the scoped directory, so it always failed its first attempt |
| I17 | I2 | IMPORTANT | PROVEN_EXECUTION | FIXED | the acceptance matrix hardcoded and created the scoped bundle path, so no row claiming command-level generation could observe `resolve_bundle_dir` at all |
| I18 | — | IMPORTANT | PROVEN_EXECUTION | FIXED | the same split at the implementation/post-fix stages: after any withdrawal the resolver flipped back to flat while the generator still wrote scoped |
| O24 | 3 | OPTIONAL | PROVEN_EXECUTION | FIXED | `/approve-review`'s takeover never compared the plan-approval journal's own work item against the command's resolved target |
| O25 | 4 | OPTIONAL | PROVEN_EXECUTION | FIXED | the withdrawal crash window left a scoped `REJECTED` marker that every later consumer resolved past, failing open |

`O25` was reported as possibly cosmetic and was not: it fails **open**,
not closed. It is recorded `FIXED` rather than `ACCEPTED` because closing
it cost one shared line, not the transaction machinery the review
(correctly) ruled out — `I18`'s repair to the layout rule both resolvers
already share closes it as a consequence, which the control arm confirms.

### Convergence pass 11 — fourth independent review

Raised by a fourth independent reviewer against `6431b70` (`REVISE`: 0
Blocking, 1 Important, 3 Optional). The Important finding was
independently reproduced end to end — through the real
`/apply-functional-review` bounded-fix semantics, the real
`resolve_bundle_dir`, the real `scripts/prepare-ai-review.sh`, the real
state machine and a disposable scratch repository — before anything was
changed.

| ID | Reviewer | Severity | Confidence | Status | One line |
|---|---|---|---|---|---|
| I19 | Important | IMPORTANT | PROVEN_EXECUTION | FIXED | `/apply-functional-review`'s bounded-fix branch drives a `post-fix` generation while naming none of the author-written preconditions that generation hard-requires |
| O26 | 2 | OPTIONAL | PROVEN_EXECUTION | FIXED | the acceptance matrix's control-arm figures counted skipped rows as green passes, mixed row counts with `unittest` sub-test counts, and attributed a pre-existing row to pass 10's additions |
| O27 | 3 | OPTIONAL | PROVEN_EXECUTION | FIXED | no suite ever executed a successful generation through the omitted-`[work-item-id]` flat compatibility path it documents as supported |
| O28 | 4 | OPTIONAL | PROVEN_STATIC_CLOSURE | SUPERSEDED by `O33` | `/bootstrap-workflow-v2` can reproduce the first-generation split in isolation, but is unreachable at current `HEAD` and fails closed through independent guards — pass 12 replaces this narrative disposition with an executed one |

The reviewer also predicted that the generation-driving command census
was wrong — that the conformance test enumerated four commands and
omitted `/apply-functional-review`. That was correct, and the real
population is **eight**, not five: `/recover-implementation-provenance`,
`/prepare-review` and `/bootstrap-workflow-v2` also instruct their own
run of the generator. The census is now discovered mechanically from the
command corpus rather than hand-maintained (`I19`'s regression half).


---

`I10` keeps its id — ledger ids are never renumbered — but not its
pass-7 severity. It was first written up as `IMPORTANT`/`ACCEPTED`, which
was wrong on both counts and produced a ledger that contradicted its own
audit summary (§11.6, "zero new Important findings"). Nothing in it is a
state-integrity hole: the command fails closed, the gate simply cannot be
entered. It is a **dead contract** — a documented, user-invocable command
that could only ever refuse — which is a maintenance and
operator-confusion cost, i.e. `OPTIONAL`. Convergence pass 8 resolved it
by retiring the command, so it is now `FIXED`, not `ACCEPTED`.


---

### Convergence pass 12 — fifth independent review, then its own re-audit

Raised by a fifth independent reviewer against `5d2337e` (`REVISE`: 0
Blocking, 1 Important, 3 Optional). The Important finding (`N1`) was
independently reproduced against process and product work items, over
implementation-, post-fix- and plan-published scoped bundles, before
anything was changed.

The pass then re-audited the repaired dependency closure and, separately,
ran a mechanical audit of this suite's own command-standing-in helpers:
for each helper, every `workflow_state`/`workflow_fingerprint` entry point
it calls was checked against the owning command file's text. Five calls
no owning command named; two were real supported-path contract gaps
(`I21`, `I22`) whose rows had stayed green *because* the helper performed
the step, and one was a helper stricter than its command, making a
documented approval basis unreachable end to end (`O35`). That is ledger
`I19`'s class, three more times.

| ID | Reviewer | Severity | Confidence | Status | One line |
|---|---|---|---|---|---|
| I20 | `N1` | IMPORTANT | PROVEN_EXECUTION | FIXED | `prepare-ai-review.sh` gated finalization on a `MANIFEST.md` *file existing*, so a `functional-review` generation withdrew and quarantined a valid published bundle it had no identity in |
| I21 | — | IMPORTANT | PROVEN_EXECUTION | FIXED | `validate_technical_approval_commit` had no production caller anywhere; a malformed technical-approval commit was accepted by every live consumer |
| I22 | — | IMPORTANT | PROVEN_EXECUTION | FIXED | `/apply-plan-review` regenerates the registry but never said to re-embed the plan document's generated checkpoint table, so a revision published a document the registry contradicts |
| O29 | `N2` | OPTIONAL | PROVEN_EXECUTION | FIXED | `_MOVING_COUNTER_DRIVERS` was a second hand-maintained census behind the mechanical one; a driver absent from it owed no marker line at all |
| O30 | — | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | a non-manifest stage inherits the previous round's `MANIFEST.md`, whose `bundle_id` no longer describes the directory |
| O31 | `N3` | OPTIONAL | PROVEN_EXECUTION | FIXED | no generation driver named any computation for the `review_content_id` it requires the author to state |
| O32 | `N4` | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | the implementation/post-fix given-id split on a fresh tree — fail-closed, self-healing, one retry |
| O33 | `N5` | OPTIONAL | PROVEN_EXECUTION | ACCEPTED | `/bootstrap-workflow-v2` past its retirement condition — now proven unreachable *and* fail-closed rather than asserted in a comment |
| O34 | `N6` | OPTIONAL | PROVEN_STATIC_CLOSURE | FIXED | `MILESTONE_WORKFLOW.md` documented four never-persisted phases as if they were resumable states |
| O35 | — | OPTIONAL | PROVEN_EXECUTION | FIXED | the acceptance matrix's own helper made the `USER_OVERRIDE` approval basis unreachable end to end |
| O36 | — | OPTIONAL | PROVEN_EXECUTION | FIXED | the helper-vs-command audit that found `I21`/`I22`/`O35` was a one-off sweep, preventing nothing |
| O37 | — | OPTIONAL | PROVEN_EXECUTION | FIXED | no bundle file but `MANIFEST.md` may carry a `bundle_id:` line, and nothing said so — while the feedback protocol asks the reviewer to quote that very value |

`O30`, `O32` and `O33` are recorded `ACCEPTED`, not `CLOSED`: each is a
real, reproduced property left in place with stated evidence that it is
non-gating, and each now carries an executed row rather than a claim.

## B1 — `same_content` post-fix republication can never publish its bundle

- **Description.** `record_bundle_generation(..., outcome="same_content")`
  deliberately pins `reviewed_implementation_head` and
  `implementation_revision` while the durability commit moves the
  generation head. `scripts/prepare-ai-review.sh`'s round-identity
  preflight (GPT-R43-001) then reads the *previous* `MANIFEST.md`'s
  `reviewed_implementation_head` (which is that manifest's generation
  head, see `I1`), sees it differ from the new generation head, and
  demands `implementation_revision` advance by exactly one. It has not,
  by contract. Generation is refused, permanently.
- **Affected lifecycle path.** `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
  → `APPLYING_REVIEW_FEEDBACK` → excluded-only remediation → `post-fix`
  generation. `/apply-implementation-review` step 7.
- **Severity** `BLOCKING` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Scratch repository, real script boundary:
  plan → approve → CP1 → `implementation` round 1 → `REVISE` →
  excluded-only remediation commit → `resolve_bundle_generation_outcome`
  returns `("same_content", t)` → `record_bundle_generation` +
  supersession commit → `prepare-ai-review.sh <base> post-fix <id>`
  exits 1 with
  `reason: new head '<S2>' (previous bundle was '<D1>') requires
  implementation_revision to advance by exactly one, from 1 to 2, got 1
  (GPT-R43-001)`.
  The durable state is left at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
  with a stale on-disk bundle, and no sanctioned command can move
  forward: `/recover-implementation-provenance` refuses with
  `ImplementationProvenanceRecoveryNotApplicableError` ("already the
  current `Workflow-Bundle-Generation-Record` commit — nothing to
  recover"), `/approve-review implementation` refuses on
  `assert_local_generation_matches`, and regeneration keeps failing
  deterministically. This is exactly the wedge the live
  `workflow-v2-3-1` item is in at `79afd07`.
- **Root cause.** Cluster `C1`: round identity keyed on the generation
  head rather than on the reviewed round.
- **Related findings.** `B2`, `B3` (same rule, other commands); `I1`
  (the name collision that made the rule look right); `I5` (why no test
  caught it).
- **Planned fix.** Repair `R1` — replace the preflight's
  "new head ⇒ exactly +1" inference with a revision-monotonicity rule
  (`prev` or `prev + 1`), where staying at `prev` at a new head is
  admitted **only** when the provenance-interval half of the same
  preflight actually verified the interval. Key the "same head"
  idempotence branch on the unambiguous `generation_head:` line.
- **Regression evidence.** `workflow_acceptance_matrix_test.py` rows B3 (both work-item types), B4, D1 and G1. With only the `R1` hunk of `prepare-ai-review.sh` reverted to `9299e3e`, 14 rows fail, including every `same_content` row for both types, both provenance-recovery rows, the disposition-only row and the live-wedge replay. Additionally verified against real repository history: a disposable clone at `79afd07` with the previous round's real `generation_head` (`09e662d`) and `implementation_revision: 2` restored, the shipped pre-repair script exits 1 with exactly the campaign's reported reason, and the repaired tooling exits 0, publishes, leaves the durable state untouched and satisfies `verify_implementation_provenance_interval` and `assert_local_generation_matches`.
- **Fix commit.** `09afc38`
- **Status.** FIXED

---

## B2 — `/recover-implementation-provenance` can never publish its bundle

- **Description.** Step 6 of the command regenerates the bundle at the
  new `S2` tip. `S2` is a new generation head with
  `implementation_revision` unchanged by contract, so the identical
  GPT-R43-001 rule refuses. The command has therefore never been able to
  complete the operation it exists for.
- **Affected lifecycle path.** `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
  + a legitimate excluded-only commit landing past `T`.
- **Severity** `BLOCKING` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Scratch repository: round 1 generated at
  `T`; one excluded-only commit; `verify_implementation_provenance_recovery`
  returns `t == T`; `apply_implementation_provenance_recovery` + the
  three-trailer `S2` commit; step 6's
  `prepare-ai-review.sh <base> implementation <id>` exits 1 with
  `requires implementation_revision to advance by exactly one, from 1 to
  2, got 1`.
- **Root cause.** Cluster `C1`.
- **Related findings.** `B1`, `B3`.
- **Planned fix.** Repair `R1` (same one-line rule change covers it).
- **Regression evidence.** `workflow_acceptance_matrix_test.py` row B5 (both work-item types): the recovery precondition, the `S2` commit, step 6's regeneration succeeding, `assert_local_generation_matches` passing afterwards, and recovery correctly becoming a no-op once HEAD is already the record commit.
- **Fix commit.** `09afc38`
- **Status.** FIXED

---

## B3 — functional bounded-fix `same_content` round hits the same refusal

- **Description.** `/apply-functional-review`'s bounded-code-change
  branch calls `resolve_bundle_generation_outcome` and may legitimately
  resolve `("same_content", t)` (its own text names the case: "the fix
  nets out byte-identical to the currently-reviewed round, e.g. reverted
  before publication"). It then runs
  `prepare-ai-review.sh <base> post-fix <id>`, which refuses for exactly
  the reason `B1` documents — while `technical_approval` has already
  been flipped to `STALE` before the first edit, so the item is left with
  no `CURRENT` approval, no publishable bundle, and no reachable
  regeneration path.
- **Affected lifecycle path.** `AWAITING_FUNCTIONAL_REVIEW` →
  bounded-fix → `post-fix` generation.
- **Severity** `BLOCKING` — **Confidence** `PROVEN_STATIC_CLOSURE` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Static closure: the branch reaches the exact
  same `prepare-ai-review.sh` invocation with the exact same state shape
  `B1` reproduces (`live_revision == prev_revision`, new generation
  head). `B1`'s executed reproduction is the shared proof; the
  acceptance matrix executes this branch directly as well.
- **Root cause.** Cluster `C1`.
- **Planned fix.** Repair `R1`.
- **Regression evidence.** `workflow_acceptance_matrix_test.py` row C3: the bounded-fix branch's own `same_content` outcome from `AWAITING_FUNCTIONAL_REVIEW` with a `STALE` technical approval, publishing and then re-approving.
- **Fix commit.** `09afc38`
- **Status.** FIXED

---

## B4 — generated artifact declarations make the first implementation bundle impossible

- **Description.** `/milestone-plan` step 3 is the sole sanctioned writer
  of `<work_item_id>-artifacts.json`, via
  `workflow_state.generate_artifacts_declarations`. That generator
  inherits a mature `plan_stage` classification but emits an
  `implementation_stage` classification whose only entry is the
  declarations file protecting itself:
  `protected_prefixes`/`excluded_paths`/`excluded_prefixes` are all
  empty. `classify_path_implementation_stage` fails closed on anything
  else, so the first implementation-stage computation for the item —
  `resolve_bundle_generation_outcome`, the manifest writer,
  `any_protected_path_dirty`, or the provenance-interval walk — raises
  `UnclassifiedPathError` on the item's own deliverable. The generator is
  also never given `work_item_type`, so it could not emit a
  type-appropriate default even in principle.
- **Affected lifecycle path.** Every work item, both types. The failure
  surfaces only *after* the plan-approval hard gate, at
  `SELF_REVIEWING_IMPLEMENTATION` → first bundle.
- **Severity** `BLOCKING` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `GENERIC_DESIGN`
- **Reproduction / proof.** Scratch repository, product work item
  `milestone-9`, created strictly through `route_work_item` /
  `generate_registry` / `generate_mapping` /
  `generate_artifacts_declarations` / `publish_plan_revision`: the plan
  bundle generates, the two plan-review stages record, the plan approval
  commits, `CP1` adds `app/src/main/kotlin/Feature.kt`, and the
  implementation bundle raises
  `UnclassifiedPathError: app/src/main/kotlin/Feature.kt`.
  Secondary proof, same class: `/prepare-functional-review` unavoidably
  writes and commits `docs/ACTIVE_MILESTONE.md`
  (`workflow_state.FUNCTIONAL_CHECKLIST_PATH`, hardcoded), so a
  declaration that does not classify that path breaks every
  implementation-stage computation from the functional gate onward — a
  path the generated default also never names.
- **Root cause.** Cluster `C3`: plan-stage defaults inherit a mature,
  reviewed set; implementation-stage defaults inherit nothing, and the
  generator is type-blind.
- **Related findings.** `I3`/`I4` are the same "the sanctioned path does
  not produce a working artifact" shape at the bundle-input layer.
- **Planned fix.** Repair `R3` — give
  `generate_artifacts_declarations` the work item's `work_item_type`
  and emit a type-appropriate, fail-closed `implementation_stage`
  default: the workflow's own guaranteed-written paths classified
  explicitly, the item's own deliverable prefix `protected`, and the
  opposite type's territory `excluded`. Fail-closed behaviour for
  genuinely novel paths is preserved.
- **Regression evidence.** `workflow_acceptance_matrix_test.py` rows F1/F1b (both types, no `artifacts=` override anywhere in the suite) plus `TestArtifactsDeclarationsGeneratorIsUsableAtBothStages` (9 tests, confirmed to fail against the pre-fix sources at `09afc38`): both types' deliverable trees, every workflow-written path, the declarations file's own self-protection surviving the registry exclusion, and fail-closed behaviour preserved for a genuinely novel path.
- **Fix commit.** `8357128`
- **Status.** FIXED

---

## B5 — generated plan-stage declarations leave three documents unclassified

- **Description.** `generate_artifacts_declarations`' `plan_stage` half
  inherits `fingerprint.PLAN_STAGE_EXCLUDED_PATHS`/`..._PREFIXES`
  verbatim. Those sets were authored as the *complement* of
  `fingerprint.PLAN_STAGE_PROTECTED`, but the template's own
  `protected_paths` is a different, smaller set — the item's own three
  declaration paths. The three documents `PLAN_STAGE_PROTECTED` names
  (`docs/ai-workflow/WORKFLOW_V2_PLAN.md`,
  `docs/ai-workflow/WORKFLOW_V2_AUDIT.md`, `docs/TECHNICAL_DECISIONS.md`)
  therefore land in neither set, and `classify_path` fails closed the
  moment any of them changes.
- **Affected lifecycle path.** Plan-stage generation for any freshly
  created work item. `docs/TECHNICAL_DECISIONS.md` in particular is a
  document `/milestone-plan` step 5 explicitly directs the planner to
  engage with, and any concurrent work item may touch the other two.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `GENERIC_DESIGN`
- **Reproduction / proof.** Scratch repository, fresh work item created
  through the sanctioned generators: all three classify `UNCLASSIFIED`;
  after an ordinary `docs/TECHNICAL_DECISIONS.md` edit, plan-bundle
  generation raises
  `UnclassifiedPathError: docs/TECHNICAL_DECISIONS.md`.
- **Root cause.** Cluster `C3`, plan-stage half: an inherited set that
  is only complete relative to a protected set the template does not
  reproduce.
- **Related findings.** `B4` is the same generator's implementation-stage
  half.
- **Planned fix.** Repair `R3` — the template excludes the frozen
  documents by exact path, as another work item's plan-stage content,
  unless one of them *is* this item's own declared plan/registry/mapping
  path, in which case `classify_path`'s protected-first ordering keeps it
  protected.
- **Regression evidence.**
  `test_plan_stage_default_classifies_the_frozen_protected_documents`,
  `test_plan_stage_default_keeps_an_own_declaration_path_protected`,
  `test_plan_stage_default_still_fails_closed_on_a_novel_path`, and
  acceptance-matrix row
  `test_f1_generated_declarations_are_usable_for_a_plan_touching_decisions`.
- **Fix commit.** `8357128`
- **Status.** FIXED

---

## B6 — `/milestone-plan` never stages the files the resolver requires to be tracked

- **Description.** `resolve_plan_stage_metadata` — the resolver every
  plan-stage read goes through, including `prepare-ai-review.sh`'s own
  plan-stage preflight — requires each declared
  `plan_path`/`registry_path`/`mapping_path` to be **in the Git index**
  (`_validate_plan_stage_metadata_path`'s tracked-path check,
  `GPT-R32-001`/`GPT-R33-002`). A freshly created work item's four
  plan-stage files are new and untracked when `/milestone-plan` finishes,
  and by design the plan-approval commit is what commits them. The
  command has no staging step, so nothing bridges the two: the very next
  step's generator refuses before writing any bundle content.
- **Affected lifecycle path.** Every fresh work item's first plan bundle
  — the exact situation the campaign's own trigger describes ("the first
  real RepFlow product milestone").
- **Severity** `BLOCKING` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `GENERIC_DESIGN`
- **Reproduction / proof.** Scratch repository, `/milestone-plan`
  executed exactly as written: `prepare-ai-review.sh <base> plan <id>`
  exits 1 with `InvalidPlanStageMetadataPathError: plan_path '<path>' is
  not a tracked path`, and no bundle content is written. With the four
  files marked intent-to-add first, the whole plan lifecycle completes
  and produces a five-member approval commit — the same shape as the live
  `718f619`. Real history corroborates the premise: `git log
  --diff-filter=A` shows both `workflow-v2-3`'s and `workflow-v2-3-1`'s
  plan documents were first added by their own plan-approval commit.
- **Root cause.** Cluster `C9`: a load-bearing precondition assumed by
  the code and never stated by the command.
  `workflow_state.py` twice names "`/milestone-plan`'s own staging step"
  — in `DirtyIndexBeforeStagingError` and in
  `stage_plan_approval_commit_paths`, which promises never to reject
  "this work item's own legitimate pending intent-to-add paths" — for a
  step that did not exist.
- **Planned fix.** Repair `R8` — `/milestone-plan` step 3 marks the four
  files intent-to-add (`git add -N`) and leaves them that way;
  `/apply-plan-review` step 5 points at the same step for a revision that
  creates one of them for the first time. The resolver's tracked-path
  requirement is not relaxed: doing so would let an untracked file
  dropped anywhere in the worktree become authoritative metadata, which
  is exactly what `GPT-R32-001` closed. Intent-to-add is the correct
  form — `git diff --name-only --cached HEAD` does not report an unstaged
  intent-to-add marker, verified empirically, so
  `stage_plan_approval_commit_paths`' own pre-staging index-isolation
  check still starts from a clean index.
- **Regression evidence.** Acceptance-matrix row A5 (the refusal and its
  remedy) plus row A1's assertion that the plan-approval commit's member
  set is exactly the five files `718f619` itself carries; the whole suite
  now leaves all four plan-stage files uncommitted and intent-to-add, so
  every row exercises the conditional fifth member. Command text pinned
  by `test_milestone_plan_step3_names_the_intent_to_add_staging_step` and
  `test_apply_plan_review_step5_points_at_the_same_staging_step`.
- **Fix commit.** `04b93d2`
- **Status.** FIXED

---

## B7 — a sibling work item's plan-stage content is unclassified for every other item

- **Description.** The generated template's plan-stage exclusions name
  specific `docs/ai-workflow/*.md` files and the
  `registry/`/`requirements/`/`archive/`/`dry-run/` prefixes, but nothing
  covers *another* work item's own declared plan document. Two work items
  live in one repository routinely — `D1` calls concurrent activity "the
  normal case, not an execution lock" — so the moment a sibling's plan
  document changes since this item's `base_commit`, this item's
  classification fails closed.
- **Affected lifecycle path.** Plan-stage generation and every
  implementation-stage computation, for any item that is not the only one
  in the repository.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `GENERIC_DESIGN`
- **Reproduction / proof.** Scratch repository with a `process` item and
  a `product` item created strictly through the generators: after the
  process item's plan lands, the product item's own plan-stage
  computation raises
  `UnclassifiedPathError: docs/ai-workflow/proc-item-plan.md`.
  Corroborated by this repository's own history — every real work item
  hand-enumerated every sibling plan document in its declaration, and
  `workflow-v2-3-1-artifacts.json` records one such miss caught only at
  round-3 plan review ("missed in the prior revision's otherwise-identical
  sweep of sibling plan documents").
- **Root cause.** Cluster `C3`: an inherited classification vocabulary
  that is complete only for the single-work-item case, restated per item
  by hand.
- **Related findings.** `B4`/`B5` are the same generator's other two
  gaps. This one was found only after `B4`/`B5` were repaired and the
  matrix grew a genuinely concurrent fixture.
- **Planned fix.** Repair `R3` extension — exclude `docs/ai-workflow/` at
  both stages, for both types. Protected-first ordering keeps every
  own-declaration path intact (own plan/registry/mapping stay plan-stage
  `protected`; own artifacts file stays implementation-stage
  `protected`), and fail-closed is unchanged for any path under a
  directory the template does not name.
- **A judgment made, not deferred.** This is the one classification
  decision the template now makes rather than leaving to
  `SELF_REVIEWING_PLAN`, and it is stated as such in
  `_implementation_stage_default`'s docstring and in `/milestone-plan`
  step 3. The alternative — leave it unclassified, let the planner
  decide — was the template's behaviour earlier in this same campaign and
  is exactly what this finding disproves. A `process` item whose
  deliverable genuinely *is* a workflow design document must move that
  exact path into `implementation_stage.protected_paths`, which
  protected-first ordering then honours.
- **Regression evidence.** Acceptance-matrix row D14 (two typed work
  items running concurrently end to end, neither staling nor
  unclassifying the other, focus never stolen, the pointer released on
  completion), plus
  `test_a_sibling_work_items_plan_document_is_classified_at_both_stages`
  and `test_a_new_workflow_design_document_is_excluded_not_unclassified`.
- **Fix commit.** `cc195aa`
- **Status.** FIXED

---

## I1 — `MANIFEST.md`'s `reviewed_implementation_head` is not `WORKFLOW_STATE.json`'s

- **Description.** `render_manifest_md_implementation_stage` writes the
  generation head into a field named `reviewed_implementation_head`, and
  the identical value into `generation_head`. `WORKFLOW_STATE.json`'s
  field of the same name means something else: the commit whose protected
  content the round reviews. Under the documented protocol the durability
  commit always lands *before* generation, so the two values differ by at
  least one commit on **every** ordinary round, and by more after a
  same-content round. `/approve-review implementation` writes the
  *state's* value into `technical_approval.reviewed_content_commit`, so
  the approval record and the bundle disagree under one name.
- **Affected lifecycle path.** Every implementation/post-fix bundle.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Scratch repository, healthy round 1:
  `MANIFEST.md` records `reviewed_implementation_head: d8621fa4…` and
  `generation_head: d8621fa4…` while `WORKFLOW_STATE.json` records
  `reviewed_implementation_head: 30e4fdf8…` (the checkpoint commit).
  `scripts/workflow_fingerprint_generalization_test.py` asserts this
  divergence as intended behaviour, which is what let `B1`'s rule be
  written against the wrong field.
- **Root cause.** Cluster `C2`.
- **Planned fix.** Repair `R2` — make the implementation-stage manifest
  report the *state's* `reviewed_implementation_head` under that name,
  keep `generation_head` as the commit the digest was measured at, and
  point the preflight's idempotence branch at `generation_head`.
- **Regression evidence.** Acceptance-matrix row B1 asserts the manifest's two head fields name two different commits and that `technical_approval.reviewed_content_commit` equals the state's value; rows B3/B5 assert the pinned head survives a republication. Two existing assertions in `workflow_fingerprint_generalization_test.py` that pinned the collision as intended behaviour are corrected.
- **Fix commit.** `09afc38`
- **Status.** FIXED

---

## I2 — non-string `work_item_type` escapes as a raw `TypeError`

- **Description.** `workflow-v2-3-1` CP1's round-1 remediation added an
  `isinstance(work_item_type, str)` guard to
  `resolve_plan_stage_metadata`'s inline condition-2 check, because that
  reader "deliberately reads raw, unvalidated JSON". The shared validator
  `validate_work_item_type` — present in *both*
  `workflow_state.py` and `workflow_fingerprint.py` and reached by every
  implementation-stage reader — was not hardened, so the same corrupt
  value produces `TypeError: cannot use 'list' as a set element` instead
  of the documented `InvalidWorkItemTypeError`.
- **Affected lifecycle path.** Every implementation-stage read:
  `write_manifest_with_verified_identifiers_implementation_stage_for_work_item`,
  `approval_review_content_id`, `resolve_bundle_generation_outcome`,
  and therefore `prepare-ai-review.sh` at the `implementation`/`post-fix`
  stages.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `V2_3_1_SPECIFIC` (the remediation that created the asymmetry)
- **Reproduction / proof.** Scratch repository, healthy round 1, then
  `work_item_type` set to `["process"]` in `WORKFLOW_STATE.json`:
  `resolve_plan_stage_metadata` → `PlanStageNotApplicableError` (correct);
  `write_manifest_with_verified_identifiers_implementation_stage_for_work_item`
  → `TypeError`; `resolve_bundle_generation_outcome` → `TypeError`;
  `prepare-ai-review.sh <base> implementation <id>` → exit 1 with a raw
  `TypeError` traceback.
- **Root cause.** Cluster `C7`: a fail-closed hardening applied at one
  call site instead of at the shared validator.
- **Planned fix.** Repair `R4` — harden `validate_work_item_type` in both
  modules to reject a non-`str` with `InvalidWorkItemTypeError`, which
  fixes every caller at once. Same treatment for
  `validate_work_item_id`'s regex call.
- **Regression evidence.** `TestNonStringIdentityValuesFailClosed` (3 tests, 7 errors against the pre-fix sources at `4456457`), `test_non_string_work_item_kind_raises_the_documented_error`, `test_validate_state_refuses_a_non_string_work_item_kind`, and acceptance-matrix rows E1/E2/E3, which additionally assert the real script boundary never emits a bare `TypeError:` line.
- **Fix commit.** `9299e3e`
- **Status.** FIXED

---

## I3 — plan-stage commands omit `TEST_RESULTS.md`'s required marker lines

- **Description.** `finalize_bundle_generation` hard-requires a plan-stage
  `TEST_RESULTS.md` opening with `stage: plan (revision N)` and
  `head: <40-hex>`, and *withdraws* the bundle (quarantines `current/`,
  deletes the archive) when they are missing.
  `/milestone-plan` step 6 lists `PLAN.md`, `CONTEXT_FILES.txt` and
  `REVIEW_REQUEST.md` and never mentions `TEST_RESULTS.md` at all;
  `/apply-plan-review` step 5 names only `REVIEW_REQUEST.md` before
  re-running the generator, even though both marker values change every
  round.
- **Affected lifecycle path.** Every plan-stage generation and
  regeneration.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Scratch repository, `/milestone-plan` step 6
  executed exactly as written: `prepare-ai-review.sh <base> plan <id>`
  exits 1, `status: withdrawn`, `current/` quarantined to
  `current.rejected-<token>/`, archive deleted.
- **Root cause.** Cluster `C4`.
- **Planned fix.** Repair `R5` — name the complete author-written input
  set, including both marker lines and their values, in the four
  commands that drive a generation.
- **Regression evidence.** Acceptance-matrix row A4 (the withdrawal, the quarantine directory, and a clean regeneration afterwards) and `TestGenerationCommandsNameTheCompleteAuthorInputSet`, each needle independently confirmed absent from the pre-fix command text.
- **Fix commit.** `8357128`
- **Status.** FIXED

---

## I4 — implementation-stage commands omit `IMPLEMENTATION_SUMMARY.md`'s required marker

- **Description.** The same shape as `I3` at the implementation stage:
  `finalize_bundle_generation` requires
  `implementation_revision: <N>` in `IMPLEMENTATION_SUMMARY.md`, matching
  the live counter, and withdraws the bundle otherwise.
  `/milestone-implement` step 4 says only "write
  `IMPLEMENTATION_SUMMARY.md` (what was built, per checkpoint, and why)";
  `/apply-implementation-review` step 7 never mentions refreshing the
  line even though the counter advances on every ordinary round.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Scratch repository, `/milestone-implement`
  step 4 executed exactly as written: exit 1, `status: withdrawn`,
  `message: … IMPLEMENTATION_SUMMARY.md states no 'implementation_revision: N'
  line -- expected 1`, `current/` quarantined.
- **Root cause.** Cluster `C4`.
- **Planned fix.** Repair `R5`.
- **Regression evidence.** Acceptance-matrix row B6 and the same conformance class.
- **Fix commit.** `8357128`
- **Status.** FIXED

---

## I5 — the `same_content`/recovered generation modes have no test through the real script

- **Description.** `same_content` and the recovered role are covered only
  at the helper level (`record_bundle_generation`,
  `resolve_bundle_generation_outcome`,
  `verify_implementation_provenance_*` in
  `scripts/workflow_state_test.py`). No test anywhere drives either mode
  through `scripts/prepare-ai-review.sh`. Worse, the one suite that does
  drive the script for round identity
  (`workflow_fingerprint_generalization_test.py`,
  `TestRoundIdentityPreflight`) builds its state by writing
  `WORKFLOW_STATE.json` directly — never through
  `record_bundle_generation` — and its
  `test_new_head_with_stale_revision_refuses` encodes `B1`'s defect as
  the specification.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_STATIC_CLOSURE` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** `grep -c same_content scripts/*_test.py`:
  `workflow_state_test.py` only (helper level);
  `workflow_integration_test.py`'s single hit is a docstring sentence;
  `workflow_fingerprint_test.py`'s two hits are unrelated identifiers.
  No file contains both `same_content` and a `prepare-ai-review.sh`
  subprocess invocation in the same test.
- **Root cause.** Cluster `C8`: the lifecycle's non-ordinary modes were
  built helper-first and never given an end-to-end fixture.
- **Planned fix.** Repair `R6` — a durable, re-runnable acceptance matrix
  (`scripts/workflow_acceptance_matrix_test.py`) that drives every mode
  through the real script/command boundary for both a `process` and a
  `product` work item, plus a corrected
  `TestRoundIdentityPreflight` fixture.
- **Regression evidence.** `scripts/workflow_acceptance_matrix_test.py` itself: 73 tests covering every matrix row for both work-item types through the real script/command boundary, wired into CI. `TestRoundIdentityPreflight`'s misleading fixture docstring is corrected to say why it refuses and to point at the rows that prove the legitimate counterpart.
- **Fix commit.** `c92f37d`, `04b93d2`, `4931f0b`, `abbe884`, `774f08b`
- **Status.** FIXED

---

## I6 — legacy adoption's freshness check defaults to another work item's classification

- **Description.** `GPT-R30-005` retired
  `compute_review_content_id_plan_stage`'s `PLAN_STAGE_*` defaults for one
  stated reason: "a generic caller omitting them used to silently compute
  `workflow-v2-1-core`'s own identity instead of failing loudly", and
  `OPUS-R27-002` applied the same treatment to
  `approval_review_content_id`'s `artifacts_path`. Two live defaults of
  exactly that shape survived:
  `load_implementation_stage_classification(repo_root,
  artifacts_path=DEFAULT_ARTIFACTS_PATH)`, and
  `promote_legacy_work_item`, which carried that default through into its
  own signature — even though `/prepare-functional-review` step 0a's own
  text says to pass "this item's own artifact-declarations file, not
  `workflow-v2-1-core`'s".
- **Affected lifecycle path.** `D-Legacy` phase 2 adoption
  (`LEGACY_READY` → `AWAITING_FUNCTIONAL_REVIEW`), whose only freshness
  check is the one that answers for the wrong work item.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Scratch repository, a legacy *product* work
  item whose `app/` content genuinely changed after its
  `reviewed_content_commit`: with the item's own declarations the
  adoption is correctly refused
  (`LegacyAdoptionStaleApprovalError`); with the argument omitted it is
  **promoted with a `CURRENT` technical approval**, because
  `workflow-v2-1-core`'s own implementation-stage classification excludes
  `app/`. Fail-open, from a defaulted argument.
- **Root cause.** Cluster `C7`, second instance: a rule retired at one
  call site and left live at another.
- **Planned fix.** Repair `R9` — both parameters become required, so
  omitting one is a loud `TypeError` rather than a silent wrong answer.
  `DEFAULT_ARTIFACTS_PATH` stays as a named constant, exactly as
  `PLAN_STAGE_PROTECTED` and friends remain constants after
  `GPT-R30-005`.
- **Regression evidence.** Acceptance-matrix row E13: the clean adoption,
  the correct refusal, both arguments now being required, and — by
  computing both classifications side by side — that the wrong item's
  sets really would have answered "not stale". Confirmed to fail against
  the pre-fix sources at `774f08b`.
- **Fix commit.** `e46822e`
- **Status.** FIXED

---

## I7 — an unchanged checklist cannot produce its round's evidence commit

- **Description.** `/prepare-functional-review` step 3a must create a
  round-scoped `Workflow-Functional-Checklist` evidence commit for every
  round, and `discover_current_functional_checklist_evidence` is keyed by
  `<work_item_id>/<implementation_revision>/`. A bounded functional fix
  advances that revision, so a round that legitimately needs no checklist
  change still has no evidence of its own — but the checklist file is
  then byte-identical to `HEAD` and the step's own
  `git commit -- docs/ACTIVE_MILESTONE.md` fails with `nothing to commit,
  working tree clean`. The round ends with no discoverable evidence and
  `/accept-scoped-remediation` refuses permanently
  (`MissingFunctionalChecklistEvidenceError`).
- **Affected lifecycle path.** Every functional round after the first
  whose checklist does not change — the ordinary case when a bounded fix
  does not alter what the user must re-test.
- **Severity** `IMPORTANT` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Reproduction / proof.** Scratch repository: round 1 commits its
  evidence; a bounded functional fix advances `implementation_revision`
  to 2; step 3a as written fails on the identical checklist. With
  `git commit --allow-empty` the commit lands and every downstream check
  accepts it unchanged —
  `discover_current_functional_checklist_evidence` finds it by trailer,
  `git rev-parse <commit>:docs/ACTIVE_MILESTONE.md` resolves to the
  inherited blob the trailer names, and
  `verify_functional_checklist_evidence`'s clean-working-tree check
  passes.
- **Root cause.** Cluster `C9`: the round-scoped trailer carries
  information the file itself does not, and the step's commit shape
  assumed the two always move together.
- **Planned fix.** Repair `R10` — step 3a names the branch, and scopes
  it: the commit's payload *is* the round-scoped trailer, whose value
  differs from the previous round's even at an identical blob. Every
  other metadata-only commit in this workflow carries a real
  `WORKFLOW_STATE.json` change and an empty one would mean the state
  write never happened, so no other command may use `--allow-empty`.
- **Regression evidence.** Acceptance-matrix row C8 (two rounds,
  identical checklist, distinct evidence commits, every downstream check,
  then acceptance), confirmed to fail against a harness following the
  pre-repair step; and
  `test_prepare_functional_review_step3a_handles_an_unchanged_checklist`,
  which also asserts no other command mentions `--allow-empty`.
- **Fix commit.** `bdb0900`
- **Status.** FIXED

---

## O1 — four documented phases are never written

- **Description.** `SELF_REVIEWING_PLAN`, `FIXING_FUNCTIONAL_FINDINGS`,
  `AWAITING_TECHNICAL_APPROVAL` and `AWAITING_USER_ACCEPTANCE` are
  `KNOWN_PHASES` members with full state descriptions in
  `MILESTONE_WORKFLOW.md`, but no writer ever assigns them.
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_STATIC_CLOSURE` —
  **Scope** `BASELINE_V2_3_0`
- **Proof.** Every `work_item["phase"] = …` assignment in
  `workflow_state.py` (14 sites) enumerated in
  `WORKFLOW_SYSTEM_AUDIT.md` §1.2; none writes these four. No
  `.claude/commands/*.md` file compares `phase` against any of them
  either (`grep`), so nothing is gated on a value that can never appear.
  `milestone_complete_gate_reachable`'s docstring already records the
  `AWAITING_USER_ACCEPTANCE` half as a known, accepted gap.
- **Disposition.** Non-gating: these are narrative state names, not
  persisted values, and no reachable code path or command tests them.
  The same narrative/persisted split shows up once more, in
  `/milestone-plan` step 6's heading ("Enter
  `AWAITING_EXTERNAL_PLAN_REVIEW`"), which is the `"1"` item's target;
  a `"2.1"` item's `publish_plan_revision` writes
  `AWAITING_LOCAL_PLAN_REVIEW`, as step 3's own `[2.1]` sub-step states
  explicitly. The state file is authoritative and the `[2.1]` branch
  overrides the heading, so the only cost is a possibly-misreported
  phase in a hand-off message.
  Documented here rather than repaired — assigning them would change
  `record_bundle_generation`'s legal-source-phase contract
  (`FIXING_FUNCTIONAL_FINDINGS` in particular is deliberately *not*
  written, because the bounded-fix branch must call
  `record_bundle_generation` from `AWAITING_FUNCTIONAL_REVIEW`).
- **Status.** ACCEPTED (non-gating)

---

## O2 — `current_bundle_id` and `functional_acceptance_status` are vestigial

- **Description.** Both are initialised to `null` by `default_work_item`
  and never written by anything. `current_bundle_id` in particular reads
  as an identity field but carries no identity.
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_STATIC_CLOSURE` —
  **Scope** `BASELINE_V2_3_0`
- **Proof.** `grep -rn current_bundle_id scripts/*.py .claude/commands`
  outside test files finds only the `default_work_item` initialiser and
  unrelated *parameter* names (`resolve_approval_basis`'s
  `current_bundle_id` argument). Same for
  `functional_acceptance_status`.
- **Disposition.** Non-gating; removing either is a schema change with no
  correctness benefit, and `apply_scoped_remediation_acceptance` names
  `functional_acceptance_status` in its "leaves untouched" contract.
  Documented, not repaired.
- **Status.** ACCEPTED (non-gating)

---

## O3 — `write_registry_and_mapping` assumes the workflow directory layout exists

- **Description.** The sole sanctioned registry/mapping writer does
  `path.write_text(...)` without `mkdir -p`, so a repository adopting the
  workflow without pre-existing `docs/ai-workflow/registry/` and
  `docs/ai-workflow/requirements/` directories gets a bare
  `FileNotFoundError` out of `/milestone-plan` step 3.
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `GENERIC_DESIGN`
- **Proof.** Observed directly while building the scratch fixture:
  `FileNotFoundError: … /docs/ai-workflow/registry/proc-item-registry.json`.
- **Planned fix.** Repair `R7` — create the parent directories in the
  one writer, before writing. Bounded and clearly beneficial.
- **Regression evidence.**
  `test_write_registry_and_mapping_creates_missing_parent_directories`
  (confirmed to fail against the pre-fix sources at `4456457`) and
  acceptance-matrix row F2.
- **Fix commit.** `9299e3e`
- **Status.** FIXED

---

## O4 — two commands still instruct authoring `PLAN.md`

- **Description.** `WFR-67` made the generator derive
  `<bundle_dir>/PLAN.md` unconditionally from a pinned snapshot, and
  `prepare-ai-review.sh` excludes `PLAN.md` from its author-stub list at
  the plan stage for exactly that reason. `/milestone-plan` step 6 still
  says "write/refresh `<bundle_dir>/PLAN.md` with the actual plan" and
  `/apply-plan-review` step 3 still says to apply findings to
  "`<bundle_dir>/PLAN.md` and the real execution/reference plan doc".
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_STATIC_CLOSURE` —
  **Scope** `BASELINE_V2_3_0`
- **Proof.** `prepare-ai-review.sh` lines 173-176 (derivation) and
  302-306 (`STUB_FILES` excludes `PLAN.md` unless the stage is not
  `plan`); the two command lines quoted above.
- **Impact.** Harmless in practice (the derivation overwrites the
  hand-written file before anything reads it) but actively misleading,
  and an operator who edits only the bundle copy silently loses the edit.
- **Planned fix.** Repair `R5` (same command-text pass).
- **Regression evidence.**
  `test_milestone_plan_step6_no_longer_instructs_authoring_plan_md` and
  `test_apply_plan_review_step3_routes_edits_to_the_authoritative_plan`.
- **Fix commit.** `8357128`
- **Status.** FIXED

---

## O5 — the manual plan-review stage's hard-blocking fields have no shared parser

- **Description.** `/record-manual-plan-review` hard-blocks ingestion on
  two `REVIEW_FEEDBACK.md` fields — `Reviewer role:` and
  `review_content_id:` — that no function in `workflow_fingerprint`
  parses. The three ordinary binding fields have
  `parse_review_feedback_binding_fields`/`assert_feedback_matches_bundle`
  precisely so "feedback is never applied at face value"; these two are
  left to whatever regex the executing agent invents.
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_STATIC_CLOSURE` —
  **Scope** `BASELINE_V2_3_0`
- **Proof.** `parse_review_feedback_binding_fields` returns exactly
  `status`/`reviewed_bundle_id`/`reviewed_base_commit`/`work_item`;
  `validate_manual_plan_review_preconditions` receives `feedback_role`
  and `feedback_review_content_id` as caller-supplied arguments, and no
  parser for either exists anywhere in the two modules.
- **Disposition.** Non-gating and not repaired. Both values are checked
  strictly once supplied (`WrongReviewerRoleError`,
  `StaleReviewContentIdError`), the failure mode of a mis-parse is a
  refusal rather than a wrong approval, and adding a parser would be new
  API with no proven failure behind it — speculative hardening this
  campaign explicitly excludes. Recorded so a future round that touches
  this command can close it deliberately.
- **Status.** ACCEPTED (non-gating)

---

## O6 — `_discover_trailer_commits` resolves a single off-first-parent candidate

- **Description.** The shared trailer search short-circuits when a
  trailer value has exactly one candidate, *before* the first-parent
  tie-break, so a lone candidate reachable only across a merge resolves.
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_STATIC_CLOSURE` —
  **Scope** `BASELINE_V2_3_0`
- **Proof.** `_discover_trailer_commits`' own `if len(candidates) == 1:
  resolved[value] = candidates[0]; continue` branch, documented in that
  function's docstring as deliberate (`OPUS-R129-M03`: `verify` is a
  tie-break, never a precondition; `WF0` is the live instance that
  depends on it).
- **Disposition.** Not reachable as a correctness hole in any consumer:
  `verify_implementation_provenance_interval` independently requires live
  HEAD to *be* the resolved commit; `discover_current_functional_checklist_evidence`
  re-checks first-parent membership itself and raises
  `NonFirstParentFunctionalChecklistEvidenceError`;
  `implementing_entry_reachable` requires ordinary ancestry, which is
  exactly what `MILESTONE_WORKFLOW.md` documents for that gate; and
  `resolve_scoped_remediation_round`'s two possible outcomes for such a
  commit (`ExactReplay`, `ConflictingDuplicate`) are both conservative.
  Documented, not repaired.
- **Status.** ACCEPTED (non-gating)

---

## O7 — `/milestone-implement` has no phase guard of its own

- **Description.** Every other phase-sensitive command carries an explicit
  phase guard (`/review-plan`, `/record-manual-plan-review`,
  `/review-implementation`, `/review-functional`,
  `/recover-implementation-provenance`). `/milestone-implement` instead
  relies on `implementing_entry_reachable`, a purely content-based check.
  A plan revision applied mid-implementation
  (`/apply-plan-review` has no phase guard either) leaves the item at
  `AWAITING_LOCAL_PLAN_REVIEW`, and while that revision is *uncommitted*
  `/milestone-implement` still proceeds — `approval_is_current` is
  commit-sourced by design, "exactly what a fresh session sees, never
  uncommitted local edits". `complete_checkpoint` on the last checkpoint
  then overwrites the phase with `SELF_REVIEWING_IMPLEMENTATION`.
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Proof and containment.** Acceptance-matrix row B13 executes the whole
  sequence. It confirms three things: the uncommitted revision genuinely
  does not stale the approval; the moment it *is* committed
  `implementing_entry_reachable` goes `False` and `/milestone-implement`
  step 1a refuses; and if a session carried the revision to the terminal
  gate regardless, `/accept-milestone` refuses via
  `StalePlanApprovalRegistryReadError`, because
  `_assert_registry_covered_by_current_plan_approval` compares the live
  bytes of *every* plan-stage protected path against the approval's own
  recorded manifest.
- **Disposition.** Non-gating, and deliberately not repaired. The
  documented `IMPLEMENTING` entry condition
  (`MILESTONE_WORKFLOW.md`) names exactly three conditions — approval
  commit ancestry, `plan_approval.status == CURRENT`, and a matching
  recomputed `review_content_id` — and all three genuinely hold here, so
  this is not a contract contradiction. `D-States`' own non-circular
  design is explicit that validity is decided by recomputation, never by
  a phase value written earlier; adding a phase guard here would be a
  design change, not a repair, and the terminal gate already contains the
  outcome. Recorded so a future round that revisits the plan-revision
  lifecycle can decide it deliberately.
- **Status.** ACCEPTED (non-gating)

---

## O8 — the plan-approval mutation guard hardcoded one work item's id

- **Description.** `acquire_plan_approval_guard` wrote
  `"work_item_id": "workflow-v2-1-core"` into every guard body. The guard
  is repository-scoped — one fixed path
  (`.ai-review/runtime/PLAN_APPROVAL_MUTATION.lease`), one holder at a
  time, whichever work item is being approved — so the field was simply
  false for any other item's plan approval.
- **Severity** `OPTIONAL` — **Confidence** `PROVEN_EXECUTION` —
  **Scope** `BASELINE_V2_3_0`
- **Proof.** Acquiring the guard in a scratch repository yields a body
  naming `workflow-v2-1-core` regardless of the item. Nothing consumes
  it: `read_plan_approval_guard` validates `lease_id` alone,
  `plan_approval_guard_release_authorization_literal` names
  `lease_id`/`step`/`step_class`, and
  `plan_approval_takeover_authorization_literal` names the *journal's*
  own `work_item_id`, which is per-item and correct. So the only cost is
  a human inspecting the lease during another item's approval and reading
  a false identity.
- **Planned fix.** Repair `R11` — drop the field rather than plumb it
  through. The journal, read under the same lock, is already the
  per-item authority, and the guard has no business asserting an identity
  it does not know. This is the last instance of the "hardcoded literal
  naming one work item" class `D-Fingerprint-Generalization` spent
  several rounds eliminating elsewhere.
- **Regression evidence.**
  `TestPlanApprovalGuardCarriesNoWorkItemLiteral`, which pins the guard
  body's exact key set; confirmed to fail against the pre-fix sources at
  `3515c72`.
- **Fix commit.** `b917039`
- **Status.** FIXED

---

## D1 — a completed withdrawal removes its own `REJECTED` marker (DISPROVEN)

- **Suspicion.** After `finalize_bundle_generation` withdraws a bundle,
  `assert_bundle_not_rejected` passes — apparently defeating the refusal
  marker.
- **Evidence.** Observed by execution (both `I3` and `I4`
  reproductions). `withdraw_bundle`'s own contract states it: "Only a
  withdrawal that completes every step removes its own marker: once
  `current/` is renamed away, nothing named `current/` remains for the
  marker to protect against, and `resolve_bundle_dir` sees an ordinary
  absent bundle." A *partial* withdrawal keeps the marker and raises
  `BundleWithdrawalError`. Every consumer that would have been refused
  by the marker instead fails on the absent bundle
  (`MissingRequiredBundleFileError` from `compute_bundle_id`), which is
  the same refusal by a different name.
- **Disposition.** DISPROVEN — designed behaviour, correctly documented,
  with an equivalent refusal on every consumer path.
- **Status.** CLOSED

---

## D2 — `/prepare-functional-review`'s hardcoded checklist path (DISPROVEN as a defect)

- **Suspicion.** `workflow_state.FUNCTIONAL_CHECKLIST_PATH` is
  hardcoded to `docs/ACTIVE_MILESTONE.md`, the product-milestone
  narrative that `/milestone-implement` step 1e explicitly routes *away*
  from for a process work item ("the requirements ledger, WF4b, for a
  process item"). A process item is nevertheless forced to write its
  functional checklist there.
- **Evidence.** Real history confirms the convention works:
  `workflow-v2-3-followups` (a process item) committed four
  `Workflow-Functional-Checklist` evidence commits, all touching only
  `docs/ACTIVE_MILESTONE.md`, and its own artifacts declaration
  classifies that path `excluded` at the implementation stage so the
  writes never stale `technical_approval`.
- **Disposition.** DISPROVEN as a correctness defect — it is a narrative
  inconsistency between two command files, not a reachable failure. The
  *real* hazard it exposes (a work item whose declaration does not
  classify this path) is captured by `B4`, where it is repaired.
- **Status.** CLOSED

---

## D3 — the unwritten approval-gate phases (DISPROVEN as a weakening)

- **Suspicion.** `AWAITING_TECHNICAL_APPROVAL` and
  `AWAITING_USER_ACCEPTANCE` are two of `MILESTONE_WORKFLOW.md`'s six hard
  gates, yet no writer ever assigns either phase (see `O1`). If the gates
  were enforced by phase, they would not be enforced at all.
- **Evidence.** Neither gate is enforced by phase. `AWAITING_TECHNICAL_APPROVAL`
  is enforced by `technical_approval_gate_reachable` (latest round status,
  no dirty protected path, a verified provenance interval, no `BLOCK`
  pin), by `resolve_approval_basis`' `user_confirmation` specificity
  check, and by `/approve-review`'s `disable-model-invocation: true`.
  `AWAITING_USER_ACCEPTANCE` is enforced by
  `milestone_complete_gate_reachable` (which accepts
  `AWAITING_FUNCTIONAL_REVIEW`, the phase items actually hold), by
  `complete_work_item`'s three independent blocks, and by
  `/accept-milestone`'s own user-only guard. Acceptance-matrix rows B1,
  C1, C2, C5, E6 and D12 exercise all of them.
- **Disposition.** DISPROVEN — the gates hold; only the narrative phase
  names are unpersisted, which is `O1`.
- **Status.** CLOSED

---

# Convergence pass 7 — independent post-repair review of `cac1abe`

The reviewer returned `REVISE` with 1 Blocking, 2 Important and 6
Optional findings. Every Blocking/Important finding was independently
reproduced against the branch before any implementation changed, and the
reproduction is recorded below rather than the reviewer's description
being taken on trust.

---

## B8 — a work item with every checkpoint `COMPLETE` is wedged in `IMPLEMENTING` (reviewer `B-1`)

- **Severity.** BLOCKING · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction** (`scratchpad/repro_b1.py`, driving the real
  `Item` fixture and the real entry points the command files name; the
  durable form is acceptance-matrix row `B14`):

  1. a `process` item plans, is approved, and implements its only
     checkpoint — `complete_checkpoint` writes
     `SELF_REVIEWING_IMPLEMENTATION`;
  2. `/milestone-plan` is rerun at revision 2 (a plan-document
     correction; the checkpoint set is unchanged), the revision is
     committed, and the two-stage plan review runs again;
  3. `/approve-review plan` succeeds. `apply_plan_approval` writes
     `phase = "IMPLEMENTING"` unconditionally — which is correct: a
     re-approved plan does return the item to the implementation stage;
  4. `implementing_entry_reachable` → `True`;
     `select_next_checkpoint` → `None`;
     `resolve_checkpoint_ownership` → `(NO_CHECKPOINT, None, None)`;
  5. every outgoing lifecycle path refuses:

     ```
     record_bundle_generation(stage="implementation") -> IllegalBundleGenerationSourcePhaseError
     record_bundle_generation(stage="post-fix")       -> IllegalBundleGenerationSourcePhaseError
     enter_applying_review_feedback                    -> IllegalApplyingReviewFeedbackEntryPhaseError
     /accept-milestone                                 -> gate not reachable (phase=IMPLEMENTING, terminal=True)
     ```

  The item cannot be advanced or completed by any command. The reviewer's
  reported sequence is reproduced exactly.

- **Writer census** (mechanical, AST over every `work_item["phase"] = <const>`
  assignment in `scripts/workflow_state.py`): 14 phase-assignment sites,
  of which **exactly one** writes `SELF_REVIEWING_IMPLEMENTATION` —
  `complete_checkpoint`, line 3256, inside
  `if all(... == "COMPLETE" for cid in all_ids)`. It can only fire as a
  side effect of a checkpoint *transitioning* to `COMPLETE`.

- **Root cause.** Both `/milestone-implement` step 2 and
  `/bootstrap-workflow-v2`'s `NO_CHECKPOINT` terminal-wrap-up branch say
  "enter `SELF_REVIEWING_IMPLEMENTATION`" in prose and name **no state
  writer at all**. On the ordinary path this is invisible: the branch is
  entered on the invocation *after* the last checkpoint completed, when
  `complete_checkpoint` has already written the phase. It is fatal the
  moment the branch is entered from a real `IMPLEMENTING`.
  `select_next_checkpoint`'s own docstring asserted the gap away —
  "`complete_checkpoint` already does this the moment the last checkpoint
  completes, so a caller only ever observes `None` here on a stale/
  out-of-band re-check" — which is false for exactly this case, and is
  what both commands took at face value.

- **Intended ownership.** The `IMPLEMENTING` → `SELF_REVIEWING_IMPLEMENTATION`
  edge. `select_next_checkpoint`'s contract already said `None` "signals
  the caller to drive" it; nothing implemented the drive.

- **Repair** (`4a8c0e4`).
  - `workflow_state.enter_self_reviewing_implementation(state,
    work_item_id, registry, now)` — the phase's second writer:
    - already `SELF_REVIEWING_IMPLEMENTATION` → returns the input state
      unchanged, no `state_revision` bump. The ordinary path re-enters
      the branch on every invocation until the bundle is generated, so a
      bumping no-op would manufacture a state change out of a read-only
      re-check;
    - `IMPLEMENTING` + `registry_completion_status` terminal → the real
      transition. Same all-complete predicate `complete_checkpoint`
      applies, re-derived from the registry rather than trusted from any
      stored phase;
    - `IMPLEMENTING` + an outstanding checkpoint →
      `IncompleteCheckpointsForSelfReviewError`, carrying
      `outstanding_checkpoint_id`;
    - any other phase → `IllegalSelfReviewEntryPhaseError`.
  - `/milestone-implement` step 2 and `/bootstrap-workflow-v2`'s wrap-up
    branch both name the writer, and both require the transition to be
    **committed** when it actually happened (see the note below).
  - `select_next_checkpoint`'s false clause replaced with the reachable
    case it denied.

  None of the four forbidden shortcuts was used: no invented checkpoint,
  no out-of-band completion of an already-`COMPLETE` checkpoint, no
  weakened bundle-generation phase guard, no hand-edited state.

- **Second-order finding, surfaced by the repair and repaired with it.**
  A plan-only re-approval leaves the protected *implementation* content
  unchanged, so the next round resolves `same_content`, not `ordinary` —
  `D-Commit-Provenance`'s own "a plan-only correction requiring no
  protected implementation change" case (`GPT-R54-002`). The recovered
  role validates the generation-record commit against its **parent's
  committed phase**
  (`RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES`). With the
  transition left in the working tree only, the parent still records
  `IMPLEMENTING` and the republication refuses one step later — the wedge
  moved rather than closed. Both commands therefore commit the state when
  (and only when) the writer transitioned, exactly as step 1f already
  commits `complete_checkpoint`'s own write with the checkpoint commit. A
  no-op call commits nothing, so the ordinary path gains no extra commit.

- **Regression evidence.** Control arm: this repair's own sources with
  only `enter_self_reviewing_implementation`'s body replaced by
  `return state` (the pre-repair behaviour — the branch named no writer).
  Exactly two rows fail, `B14` and `C5`; the other 85 stay green.
  Post-fix, `B14` drives the wedge end to end and proves recovery through
  the `same_content` republication to `MILESTONE_COMPLETE`;
  `TestEnterSelfReviewingImplementation` adds 8 unit tests including the
  agreement property against `complete_checkpoint`'s own predicate.

- **Fix commit.** `4a8c0e4`

---

## I8 — the generated `product` implementation-stage template under-classifies (reviewer `I-2`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction**, with the real generator and the real bundle path. A
  `product` item created strictly through
  `generate_artifacts_declarations(..., work_item_type="product")`,
  classified against a realistic product footprint:

  | Path | impl (pre-fix) | plan (pre-fix) |
  |---|---|---|
  | `docs/UX_FLOWS.md` | **unclassified** | excluded |
  | `docs/DOMAIN_GLOSSARY.md` | **unclassified** | excluded |
  | `docs/adr/0004-*.md` | **unclassified** | excluded |
  | `docs/agent-context/*` | **unclassified** | excluded |
  | `docs/improvements/*` | **unclassified** | excluded |
  | `.github/**` | **unclassified** | excluded |
  | `build.gradle.kts` | **unclassified** | **unclassified** |
  | `settings.gradle.kts` | **unclassified** | **unclassified** |
  | `gradle.properties` | **unclassified** | **unclassified** |
  | `gradlew`, `gradlew.bat` | **unclassified** | **unclassified** |

  Driven end to end (acceptance-matrix row `F3` against the pre-fix
  template), the item reaches its first implementation bundle and raises
  `UnclassifiedPathError` — **after** the plan-approval hard gate has
  advanced durable state and the checkpoint work is committed.

- **Root cause.** The template's per-type branching was one-directional.
  `_implementation_stage_default` carried two `if work_item_type ==
  "process":` blocks that excluded a hand-listed slice of *product*
  documentation territory; the `product` branch had no mirror, so those
  paths were excluded for a process item and unclassified for a product
  one. The repository-root build files were named by neither branch and
  by **neither stage** — and the plan-stage projection is recomputed all
  the way through implementation (`implementing_entry_reachable`,
  `_assert_registry_covered_by_current_plan_approval`), so a product
  milestone bumping `build.gradle.kts` mid-implementation failed its own
  plan-stage freshness check closed as well. This is `B4`/`B5`'s cluster
  `C3` recurring in the half `B4` did not reach: `B4` gave the generator
  a `work_item_type` and a deliverable *tree*; nothing gave the product
  type its documentation and build-configuration territory.

- **Product vs process, compared deliberately rather than unified.** The
  two templates are *not* made identical. Three categories are kept
  distinct, and the comparison is what produced them:

  | Category | `product` | `process` |
  |---|---|---|
  | own deliverable | `app/`, `gradle/`, `config/`, `docs/adr/`, root build files, `docs/UX_FLOWS.md`, `docs/DOMAIN_GLOSSARY.md` → **protected** | `scripts/`, `.claude/commands/` → **protected** |
  | the other type's | `scripts/`, `.claude/commands/` → excluded | the product set above → excluded |
  | shared | `.github/`, `docs/agent-context/`, `docs/improvements/`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/PROJECT_BRIEF.md`, `docs/TECHNICAL_DECISIONS.md`, the workflow machinery → excluded for **both** | same |

  `.github/` is deliberately *shared*, not product-owned: it carries the
  CI configuration that runs the process suites and the canonical agent
  rule sources `CLAUDE.md` names, and a concurrent item of either type
  may write it. `docs/adr/` is product-owned: architecture decision
  records are product design content a product technical review binds.

- **Repair** (`3c61b4b`). The fix is the *symmetry*, not a second
  hand-list: a new `IMPLEMENTATION_STAGE_DELIVERABLE_PATHS` table (the
  exact-path half of the existing per-type prefix table),
  `_SHARED_REPOSITORY_PREFIXES` excluded for both types, and a
  `_implementation_stage_default` body with no type-specific branch left
  in it — the other type's entries become this type's exclusions by
  construction. The plan-stage template additionally excludes the product
  build files, in the template rather than by widening
  `fingerprint.PLAN_STAGE_EXCLUDED_PATHS`, so no declaration file on disk
  changes and no existing approval's identity moves (`I9`'s own rule
  applied to this repair). Import-time assertions extended: both tables
  total over `WORK_ITEM_TYPES`, no path or prefix claimed by both types,
  and the shared set claiming neither deliverable tree.

- **Conservatism, asserted rather than claimed.** Protected-path
  semantics preserved; self-protection unchanged (the declarations file
  is still `protected` by exact path, checked first); no
  whole-repository exclusion; concurrent-writer detection unweakened
  (each type still protects only its own tree); and a genuinely novel
  path (`vendor/thirdparty/blob.bin`) still raises
  `UnclassifiedPathError` — asserted directly in `F3`.

- **Regression evidence.** Control arm: this repair's sources with only
  the `I8` hunks reverted. Exactly `F3` fails, on exactly the eight
  unclassified paths plus the bundle-generation refusal; the other 85
  rows stay green. Post-fix, `F3` runs a product milestone with a
  realistic documentation and build footprint from generated
  declarations to `MILESTONE_COMPLETE`, and replay 2 confirms the
  reviewed manifest contains `docs/UX_FLOWS.md`,
  `docs/DOMAIN_GLOSSARY.md`, `docs/adr/0004-new.md`, `build.gradle.kts`
  and the deliverable, and excludes `README.md`.

- **Fix commit.** `3c61b4b`

---

## I9 — the false artifact-declaration freshness invariant (reviewer `I-1`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **The false claim**, stated twice in `scripts/workflow_fingerprint.py`
  (the module docstring and
  `load_implementation_stage_classification`): the declarations file
  "lives under `docs/ai-workflow/registry/`, already excluded at the plan
  stage, so declaring or editing it never stales the plan approval."

- **Reproduction, from code and from this branch's real history.**

  ```
  workflow-v2-3-1  recomputed 25c3a5463bf08de5...  approved 1597efbcd89bf104...
                   stored status: CURRENT           MATCH: False
  ```

  All three protected files' blobs are byte-identical to the approval's
  own recorded `review_content_manifest`. What moved the digest is commit
  `a2ac9a9`, which added `docs/ai-workflow/audit/` to
  `plan_stage.excluded_prefixes` — and whose own message states "This
  touches neither approval — `docs/ai-workflow/registry/` is plan-stage
  excluded for this item." That is the false invariant being acted on, in
  this repository, on this branch.

- **Root cause.** Each stage's `review_content_id` is a digest over that
  stage's protected content **plus the classification sets themselves**:
  `compute_review_content_id_plan_stage`'s projection carries
  `sorted(protected_paths)`, `sorted(excluded_paths)` and
  `sorted(excluded_prefixes)`. Those sets are read out of the declaration
  file. The file's *bytes* being excluded from the content projection is
  true and is a different statement.

- **Disposition of the semantics: no exemption was created.** The
  behaviour is the intended cryptographic contract — *what is excluded is
  a reviewed fact*. Making declaration edits invisible to identity is
  precisely the "widen an exclusion and re-bless the resulting digest in
  the same session, with no gate ever having seen the classification
  change" attack that `implementation_stage.protected_paths`' own
  self-protection entry (`OPUS-R25-006`/`OPUS-R26-004`) exists to
  prevent. The repair makes the *workflow* acknowledge the real
  semantics, as the reviewer directed.

- **Repair** (`032f95f`).
  1. Both `workflow_fingerprint.py` docstrings now state which half of
     the file moves which stage's digest, and that both are intended.
  2. `docs/ai-workflow/REVIEW_PROTOCOL.md` gains "Repairing an artifact
     declaration after an approval": the per-edit truth table, the repair
     procedure at each stage (before approval / after plan approval /
     after technical approval), the explicit prohibition on widening an
     exclusion to dodge a re-review, and the rule that the stored
     `status` field is a cache while recomputation is the authority.
  3. `/milestone-plan` step 3 states the same contract at the point the
     declaration is written, which is where getting it right is free.

  No implementation path was found that *relies* on the false assumption
  to make a decision — the assumption lived in prose and in operator
  reasoning. The one place it could have leaked into behaviour,
  `_assert_registry_covered_by_current_plan_approval`, is recorded as a
  boundary below rather than left implicit.

- **Boundary, asserted in `F4` rather than assumed.**
  `resolve_own_registry_completion_status` compares the live protected
  files against `plan_approval`'s own per-path manifest and reads the
  cached `status` field. A classification-only edit changes neither, so
  it does not refuse — unlike the committed registry edit `B13`
  exercises, which does. The gate that *does* refuse is
  `implementing_entry_reachable`, which recomputes. This is ledger `O12`'s
  standing cache-vs-recompute contract (the reviewer's `O-2`), left
  non-gating as directed; it is recorded here so it is a known boundary
  rather than a rediscovery.

- **Disposition of this branch's in-flight `workflow-v2-3-1` state.**
  Recorded in `WORKFLOW_SYSTEM_AUDIT.md` §9. In short: the item is
  **stale by recomputation and is not claimed `CURRENT`**; no approval
  history was fabricated and no state byte was hand-edited to clean the
  branch.

- **Regression evidence.** `F4` pins the whole contract in both
  directions. Post-fix suites all green.

- **Fix commit.** `032f95f`

---

## I10 — `/accept-scoped-remediation`'s entry precondition is unreachable

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED (convergence pass 8: the
  command is **retired**, not made reachable)

- **Classification history.** Pass 7 first recorded this as
  `IMPORTANT`/`ACCEPTED`. Both were wrong. The finding is fail-closed —
  the gate cannot be entered, so nothing incorrect is ever recorded — and
  what it actually describes is a *dead contract*: a documented,
  user-invocable command that can only refuse. That is a maintenance and
  operator-confusion cost, i.e. `OPTIONAL`, and carrying it as `IMPORTANT`
  made this ledger contradict the same pass's own "zero new Important
  findings" summary (§11.6 of `WORKFLOW_SYSTEM_AUDIT.md`). Corrected here
  rather than left as a ledger that disagrees with its own audit.

- **How it was found.** Removing `O9`'s forgery. Once acceptance-matrix
  `C5` had to reach its precondition through a real transition, no such
  transition existed.

- **The closure.** `AWAITING_FUNCTIONAL_REVIEW` has exactly two writers:
  - `apply_technical_approval`, reachable only from
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, whose only entries are
    `record_bundle_generation` from `SELF_REVIEWING_IMPLEMENTATION`
    (which now, as of `B8`, provably requires every checkpoint
    `COMPLETE`), from `APPLYING_REVIEW_FEEDBACK`, or from
    `AWAITING_FUNCTIONAL_REVIEW` with a `STALE` technical approval — the
    last two already inside the component;
  - `promote_legacy_work_item`, which produces an item with no
    `plan_approval`, for which
    `_assert_registry_covered_by_current_plan_approval` raises
    `StalePlanApprovalRegistryReadError` rather than answering
    terminality at all.

  Growing the registry mid-flight does not open a route: the new bytes
  need a fresh plan approval, and `apply_plan_approval` returns the item
  to `IMPLEMENTING`, leaving the component.

- **Why it exists.** `D-Scoped-Remediation-Acceptance` (plan revision 22,
  `WF8B-002`) was designed for the live `workflow-v2-1-core` — a
  `governing_workflow_version: "1"` item driven by
  `/bootstrap-workflow-v2` at a time when `record_bundle_generation` had
  **no source-phase guard**. `OPUS-R101-001` later added that guard and
  made `SELF_REVIEWING_IMPLEMENTATION` the only legal source for
  `stage="implementation"`, which closed the route the design was written
  for. Real history agrees: no `Workflow-Scoped-Remediation-Acceptance`
  trailer exists anywhere in this repository, and no work item carries a
  `scoped_remediation_acceptance` record.

- **Pass 7's disposition** (superseded): ACCEPTED, non-gating, and
  explicitly not papered over —
  - the acceptance matrix no longer claimed an end-to-end scoped-
    remediation scenario it could not reach honestly. `C5` was rebuilt
    around the refusal that now guards the state (see `O9`);
  - the machinery stayed covered by unit tests:
    `workflow_state_test.py`'s `TestScopedRemediationEndToEnd`. Pass 7's
    text said "13 tests"; the class actually held **11**. Corrected here
    for the record — the class no longer exists;
  - the recommendation was to choose, in a later round, between giving the
    continued-scope entry a real writer and retiring the command with its
    gate.

- **Repair (convergence pass 8): retirement.** The second option was
  taken. Restoring reachability would mean designing a new producer for
  `AWAITING_FUNCTIONAL_REVIEW`-with-a-non-terminal-registry, which means
  either weakening `record_bundle_generation`'s source-phase guard
  (`OPUS-R101-001` — the guard that closed the route, and a real
  correctness fix) or inventing a fresh remediation lifecycle. Neither is
  warranted for a case the supported paths already cover:
  - an outstanding checkpoint that is still this milestone's scope →
    `/milestone-implement` finishes it, then the item returns to the
    functional gate and `/accept-milestone` applies unchanged;
  - a functional-review finding fixable inside the approved scope →
    `/apply-functional-review`'s **bounded** branch, which stales
    `technical_approval`, lands the fix, regenerates the `post-fix` bundle
    and re-enters external implementation review;
  - a finding of new or wider scope → `/apply-functional-review`'s
    **broad** branch, which creates a `<parent-id>-remediation-<n>` child
    work item that runs its own full cycle and blocks the parent's
    acceptance until it is itself `MILESTONE_COMPLETE`.

  **Removed** (each had exactly one caller, the retired command):
  `.claude/commands/accept-scoped-remediation.md`;
  `workflow_state.discover_scoped_remediation_commits`,
  `build_scoped_remediation_live_snapshot`,
  `verify_functional_checklist_evidence`,
  `parse_scoped_remediation_confirmation_binding_fields`,
  `resolve_scoped_remediation_round`,
  `build_scoped_remediation_live_fields`,
  `apply_scoped_remediation_acceptance`,
  `scoped_remediation_gate_reachable`,
  `SCOPED_REMEDIATION_ACCEPTANCE_FIELDS`,
  `_SCOPED_REMEDIATION_COMPARISON_FIELD_MAP`, the five classification
  outcome types (`NoExistingRound`, `ExactReplay`, `ConflictingDuplicate`,
  `MalformedAcceptanceRecord`, `AmbiguousHistory`), six exception classes
  (`AmbiguousScopedRemediationTrailerError`,
  `MissingFunctionalChecklistEvidenceError`,
  `StaleFunctionalChecklistConfirmationError`,
  `MalformedFunctionalChecklistEvidenceError`,
  `DirtyFunctionalChecklistPathError`,
  `ScopedRemediationLiveValueChangedError`), and the
  `"scoped_remediation"` member of `APPROVAL_STAGES` — a stage keyword no
  surviving command consumes must not stay in the vocabulary an operator
  can be asked to type.

  **Re-pointed, not dropped**: 37 entries in
  `docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json`
  (requirement-ledger items 171-218) named one of the deleted tests, and
  `WFO-LEDGER-COVERAGE` requires every such id to resolve and run green at
  the verified commit. They now name
  `workflow_integration_test.TestRetiredScopedRemediationLeavesNoLiveSurface`,
  each note quoting the binding it replaced. The approved reconciliation
  table and `ledger-status.json` are untouched — those requirements really
  were `IMPLEMENTED` under `WF8b`, and an approved plan table is not
  rewritten after the fact.

  **Deliberately kept** (reached by supported paths, so *not* dead):
  `discover_functional_checklist_commits`,
  `discover_current_functional_checklist_evidence`,
  `NonFirstParentFunctionalChecklistEvidenceError`,
  `AmbiguousFunctionalChecklistTrailerError` and
  `FUNCTIONAL_CHECKLIST_PATH` (`/prepare-functional-review` writes the
  checklist-evidence commit, `/review-functional` reads it, and `I7`'s
  unchanged-checklist branch depends on both);
  `registry_completion_status`, `resolve_own_registry_completion_status`,
  `milestone_complete_gate_reachable`, `complete_work_item`'s
  own-checkpoint block and `IncompleteOwnCheckpointsError` (all guard
  `/accept-milestone`); and the whole remediation-child mechanism.
  `record_bundle_generation`'s source-phase guard is untouched, and no new
  remediation lifecycle was added.

  **Operator guidance replaced, not merely deleted.**
  `IncompleteOwnCheckpointsError`'s message told the operator to "use
  /accept-scoped-remediation if this is a continued-scope remediation
  round" — following it produced a second, unrelated refusal. It now names
  the three supported paths above. The same substitution was made in
  `/accept-milestone` step 2a, `/prepare-functional-review` steps 3a/4,
  `/review-functional`, `MILESTONE_WORKFLOW.md`'s
  `AWAITING_FUNCTIONAL_REVIEW`/`MILESTONE_COMPLETE`/hard-gate sections,
  `REVIEW_PROTOCOL.md`, `WORKFLOW_V2_1_OPERATOR_REFERENCE.md` and
  `CLAUDE.md`.

- **Proof the repair binds.**
  `workflow_integration_test.py`'s
  `TestRetiredScopedRemediationLeavesNoLiveSurface` (10 tests) asserts the
  command file is gone, that no `.claude/commands/*.md` names it or the
  retired stage keyword, that every live doc mentioning it does so only in
  a paragraph that says it was retired, that no doc routes an operator to
  it, that all twenty retired symbols are absent from `workflow_state`,
  and that all thirteen surviving shared primitives are still present.
  `workflow_state_test.py`'s
  `test_refusal_message_names_only_supported_ways_forward` asserts the
  real refusal message against a real repository. Acceptance-matrix `C5`
  (ordinary acceptance), `C8` (bounded functional fix, `I7`) and `C6`
  (remediation child) all still pass, unchanged in behaviour.

---

## O9 — acceptance-matrix `C5` hand-wrote `SELF_REVIEWING_IMPLEMENTATION` (reviewer `O-6`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Status.**
  FIXED (as part of `B8`, as the reviewer directed)

- **The defect.** `test_c5_scoped_remediation_round` wrote
  `state["work_items"][wid]["phase"] = "SELF_REVIEWING_IMPLEMENTATION"`
  into `WORKFLOW_STATE.json` and committed it, so it could publish a
  `stage="implementation"` bundle while `CP2` was outstanding. It forged
  precisely the transition no command-reachable writer performed — the
  ledger row `I5` pattern exactly (*a fixture that encodes the defect as
  its specification*), one campaign later.

- **Repair** (`4a8c0e4`).
  - `Item.milestone_implement_wrap_up` performs the real step-1a/1b/1c
    sequence plus step 2's writer, and **every** `stage="implementation"`
    generation in the suite routes through it. No row can now reach a
    first-round bundle without the real transition having happened.
  - `C5` is rebuilt as
    `test_c5_outstanding_checkpoint_cannot_shortcut_the_self_review_gate`:
    it asserts the writer refuses with
    `IncompleteCheckpointsForSelfReviewError` naming `CP2`, that
    ownership resolves a concrete checkpoint rather than `NO_CHECKPOINT`,
    that bundle generation refuses from `IMPLEMENTING`, and then runs the
    honest tail — implement `CP2`, real transition, bundle, approval,
    functional gate, `/accept-milestone`.
  - The scoped-remediation scenario the old row also carried is
    withdrawn, with its reason recorded as `I10` rather than silently
    dropped. (Pass 8 then retired the command that scenario exercised, so
    there is nothing left for a future round to restore it against.)

- **Proof the repair binds.** Against the control arm with the writer
  neutralised, `C5` fails. Against the pre-repair fixture, `C5` was the
  *only* row that forged the phase — every other row already went through
  `complete_checkpoint`.

- **Fix commit.** `4a8c0e4`

---

## O10 — the completion-obligations suite was not in CI (reviewer `O-4`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Status.** FIXED

- **The gap.** `scripts/workflow_state_completion_obligations_test.py`
  covers `D1`'s `state_lock`/`state_transaction` serialization primitive
  and all of `D-Completion-Obligations` — `discover_state_writers`,
  `verify_wfo_state_serialization`, `resolve_completion_obligations`,
  `complete_work_item`'s `UnsatisfiedCompletionObligationError` gate and
  `verify_wfo_ledger_coverage`. That is `/accept-milestone`'s own verdict
  machinery. `.github/workflows/ci.yml` ran six Python suites and not
  this one, so a change breaking the release gate could reach `main` with
  every other suite green.

- **Why it qualifies** on the same terms as the suites already there, and
  why `GPT-R9-002` (the reason both `*_demo_test.py` suites are
  deliberately excluded) does not apply: 103 tests in ~4 s, stdlib-only,
  disposable scratch Git repositories, no fixed historical base commit,
  no dependence on this repository's own live `WORKFLOW_STATE.json`.

- **Fix commit.** `571b2d5`

---

## O11-O14 — the four Optional findings left non-gating, as directed

The reviewer directed that `O-1`, `O-2`, `O-3` and `O-5` be left
non-gating unless new evidence showed otherwise. Each is recorded here
with what this round actually observed, rather than with a blanket "no
change".

- **`O11` (reviewer `O-1`, a reverted protected interval cannot
  publish).** No repair in this round touched its diagnostic, and none
  naturally improved it. Left as-is, exactly as directed.
- **`O12` (reviewer `O-2`, acceptance does not recompute approval
  freshness).** New evidence *was* produced, and it is recorded rather
  than passed over: acceptance-matrix row `F4` demonstrates a
  classification-only staleness that
  `resolve_own_registry_completion_status` does **not** detect, because
  that function compares the live protected files against
  `plan_approval`'s own per-path manifest and then reads the cached
  `status` field — and a `plan_stage` classification edit changes
  neither. It does **not** show the documented contract to be unsafe:
  the gate that governs whether implementation may proceed,
  `implementing_entry_reachable`, recomputes and refuses (asserted in the
  same row). So the finding stands, its blast radius is now measured, and
  it stays non-gating. This is the row `I9` and `F4` cross-reference for
  the cache-vs-recompute contract.
- **`O13` (reviewer `O-3`, `complete_work_item` has no phase guard).**
  Unchanged. The command-level path remains proven safe by
  `milestone_complete_gate_reachable` and `/accept-milestone`'s user-only
  guard, both exercised by rows `C1`, `C5` and `B14`.
- **`O14` (reviewer `O-5`, `/review-implementation` and external review
  share `REVIEW_FEEDBACK.md`).** Unchanged.

---

## B9 — a remediation child cannot be selected by any command (reviewer `A9`)

- **Severity.** BLOCKING · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `/apply-functional-review`'s broad branch creates a
  `<parent-id>-remediation-<n>` child and tells the operator the next
  action is `/milestone-plan <child-id>`. `/milestone-plan`'s argument
  contract was `[base-sha]` — it accepted no work-item id at all, and its
  step 1 derives the target from `docs/ACTIVE_MILESTONE.md`/
  `docs/ROADMAP.md`, where a remediation child never appears. The
  instruction the runtime hands the operator was therefore not executable.

- **Why prose alone could not close it.** The gap is structural, not
  cosmetic. `create_remediation_child_work_item` deliberately does not
  repoint `active_work_item_id` (`D1`: "a resume-focus pointer, not an
  execution lock"), so a child is *never* the active item while its
  parent is open. Every command that resolves its target only from that
  pointer therefore drives the parent instead of the child. Four commands
  on the child's own supported lifecycle had no way to be pointed at it:

  | Command | Pre-fix contract | Consequence for the child |
  |---|---|---|
  | `/milestone-plan` | `[base-sha]` only | the child can never be planned |
  | `/apply-plan-review` | no arguments at all | a plan-review `REVISE` on the child cannot be applied |
  | `/apply-implementation-review` | no arguments at all | an implementation-review `REVISE` on the child cannot be applied |
  | `/accept-milestone` | no arguments at all, target never defined | the child can never be closed — **and `complete_work_item` refuses a parent with an incomplete child, so the parent is permanently unacceptable too** |

  `/apply-functional-review` read `$ARGUMENTS` but declared no
  `argument-hint`, so its own targeting was undiscoverable
  (previously recorded only as operator-reference "known discrepancy 7").

- **Reproduction, pre-fix** (`scratchpad/a9_repro.py`, then made durable
  as `workflow_integration_test.TestWorkItemTargetingContract`). Against
  `b0b5ba2`'s command files, the durable form fails exactly where the
  contradiction is:

  ```
  FAIL: test_the_broad_remediation_branch_only_names_targetable_commands
  AssertionError: unexpectedly None : /milestone-plan takes no arguments at all
  ```

  Eight of that class's ten tests fail against the pre-fix command files
  and all ten pass after the repair — the control arm was run by checking
  out `HEAD`'s `.claude/commands/` under the new tests and restoring
  afterwards.

- **Why every existing test stayed green.** Acceptance-matrix row `C7`
  *does* drive a child through a full cycle, but it constructs the child
  `Item` with `child.wid = child_id` in Python — it never asks whether a
  command contract admits that id. Row `C6` was worse: it hand-wrote
  `new_state["work_items"][child_id]["phase"] = "MILESTONE_COMPLETE"` to
  clear the parent's block, a forged terminal phase that skipped
  planning, approval, implementation and the child's own completion
  obligations, and that would have stayed green even with no way to
  select the child at all. Same defect class as `O9`.

- **Root cause.** The `[work-item-id]` targeting mechanism already existed
  and was already used by nine of fifteen commands; it was simply never
  extended to the phase-advancing commands that predate multi-work-item
  concurrency. No second selection architecture was needed or invented.

- **Fix.** The existing mechanism, extended, plus the documentation of the
  resolution rule each command now follows:

  - `/milestone-plan` gains `argument-hint: "[work-item-id] [base-sha]"`
    and a resolution rule stated in full: no argument → derive from
    `ACTIVE_MILESTONE.md`/`ROADMAP.md` as before; one argument → a
    `work_items` key selects that item, anything else is a base SHA and
    must resolve via `git rev-parse --verify <arg>^{commit}` (so the
    historical `/milestone-plan <base-sha>` form is unchanged, since a
    base SHA is never a `work_items` key); two arguments →
    `<work-item-id> <base-sha>`. Neither is guessed, and the id selects an
    **existing** entry only. The explicit-target branch lives in step 0,
    beside the other target/version resolution, so steps 1-5's
    v1-comparable text — and
    `TestGoldenV1BehaviorAgainstPreV21BaseCommit`'s real base-commit
    byte-equality hash — are untouched.
  - `/apply-plan-review`, `/apply-implementation-review` and
    `/accept-milestone` gain `argument-hint: "[work-item-id]"` and the
    step-0 "`$ARGUMENTS`, else `active_work_item_id`, else refuse" rule
    every other targeting command already states.
    `/accept-milestone` additionally must **not** derive its target from
    its own confirmation text, which would make the user-only guard
    self-satisfying.
  - `/apply-functional-review` gains the `argument-hint` matching the
    `$ARGUMENTS` it already read, and its broad branch now hands over the
    complete child sequence with the id named on every command, not just
    the first.

- **Two further defects the same closure exposed, fixed here.**
  - `/accept-milestone` step 2a listed three stops from
    `complete_work_item` and omitted `UnsatisfiedCompletionObligationError`
    entirely (the independent reviewer's `A6`). It is now documented with
    what to report.
  - `/accept-milestone` steps 3/4/5/7 update `docs/ROADMAP.md`, archive a
    milestone summary and set the next milestone's action — none of which
    a remediation child has, since it was never a roadmap milestone. A
    new step 2b routes a `parent_work_item_id`-bearing item to the
    parent's own functional-review checklist instead.

- **Regression evidence.**
  - `workflow_integration_test.TestWorkItemTargetingContract` (10 tests).
    The central rule is derived, not listed: every command file naming a
    `phase`-writing `workflow_state.py` function must declare a
    `[work-item-id]` argument, with `/bootstrap-workflow-v2` the single
    exemption — and that exemption is itself asserted to state why. A
    second test parses the child sequence out of
    `/apply-functional-review`'s own text and requires it to cover exactly
    that derived command set, so a future command that advances phase
    cannot be added without appearing there.
  - `workflow_state_test.TestPersistedPhaseWriterCensus` (5 tests): the
    same fact derived independently, by AST, as the census both the
    operator reference and the lifecycle diagram are now checked against.
  - `workflow_state_test.TestRemediationChildWorkItem.
    test_child_enters_the_plan_review_stage_its_own_governing_version_selects`:
    the child's entry phase is `publish_plan_revision`'s ordinary version
    branch (`B1` of the diagram findings), and planning it never repoints
    focus.
  - Acceptance-matrix `C7` extended: the child now survives a plan-review
    `REVISE` round applied while the parent still holds the pointer, the
    parent is asserted blocked right up to the child's own acceptance, a
    post-approval registry tamper is refused
    (`StalePlanApprovalRegistryReadError`), and the parent completes only
    afterwards. `C6`'s forged completion is gone.

- **Answers to the reviewer's five questions.**
  1. *Exact sanctioned sequence.* `/milestone-plan <child-id>` →
     `/review-plan <child-id>` → `/record-manual-plan-review <child-id>`
     → `/approve-review plan <child-id>` → `/milestone-implement
     <child-id>` (× N) → `/review-implementation <child-id>` (optional) →
     `/approve-review implementation <child-id>` →
     `/prepare-functional-review <child-id>` → `/review-functional
     <child-id>` (optional) → `/accept-milestone <child-id>`; `REVISE`
     diverts through `/apply-plan-review <child-id>` or
     `/apply-implementation-review <child-id>`, functional findings
     through `/apply-functional-review <child-id>`, and a stale generation
     head through `/recover-implementation-provenance <child-id>`.
  2. *Could an operator execute it before the fix?* No — at the first
     command.
  3. *Does the child start at `AWAITING_LOCAL_PLAN_REVIEW` for `"2.1"`?*
     Yes. It is created at `PLANNING` and `publish_plan_revision` moves it
     to `AWAITING_LOCAL_PLAN_REVIEW` for a `"2.1"` governing version, to
     `AWAITING_EXTERNAL_PLAN_REVIEW` for `"1"`.
  4. *Does the parent stay bound to the child?* Yes —
     `incomplete_children` is a reverse lookup over
     `parent_work_item_id`, and `complete_work_item` refuses the parent
     while any child is non-terminal. Focus stays on the parent
     throughout.
  5. *Can the child traverse its full lifecycle and let the parent
     complete?* Yes, now — proven end to end by the extended `C7`.

- **Fix commit.** `4866a2a`

---

## I11 — the happy path routed an implementation `APPROVE` through the `REVISE` command (reviewer `A2`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `WORKFLOW_V2_1_OPERATOR_REFERENCE.md`'s happy path read
  `/apply-implementation-review → AWAITING_TECHNICAL_APPROVAL ★ gate`, and
  its next-command table sent every
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` item there regardless of
  verdict. `/apply-implementation-review` is the `REVISE`/`BLOCK` path: its
  step 0 enters `APPLYING_REVIEW_FEEDBACK` and its step 7 republishes the
  bundle **unconditionally**.

- **Reproduction** (`scratchpad/a2_repro.py`, driving the real `Item`
  fixture and the real `prepare-ai-review.sh`): a `process` item reaches
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` with an `APPROVE` bound to
  bundle `2fe4f09e…`. Following the documented happy path:

  ```
  phase after step 0          : APPLYING_REVIEW_FEEDBACK
  step 7 resolved outcome     : same_content
  phase after step 7          : AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
  bundle_id changed           : True
  approve-review binding      : REFUSED -> FeedbackBundleMismatchError
  approval basis would be     : USER_OVERRIDE
  ```

  So the documented step does not merely fail to help — it invalidates the
  external approval that was already in hand and, if the operator forces
  past the refusal, downgrades `EXTERNAL_APPROVE` to `USER_OVERRIDE`.

- **Root cause.** The reference was written against
  `MILESTONE_WORKFLOW.md`'s state list, where
  `APPLYING_REVIEW_FEEDBACK` is the single named successor of the
  implementation-review gate, and never distinguished the verdict that
  routes there. The command itself is not wrong: applying Optional
  findings after an `APPROVE` is legitimate, and then a fresh round is
  genuinely required.

- **Fix.** The happy path goes `APPROVE` → `/approve-review
  implementation` directly; the `REVISE`/`BLOCK` diversion is stated
  separately, with the republication consequence spelled out; the
  next-command table splits the gate row by verdict; and
  `/apply-implementation-review`'s own **Caveats** now name the
  `FeedbackBundleMismatchError`/`USER_OVERRIDE` outcome. No runtime
  behavior changed — the behavior was correct, the instruction was not.

- **Regression evidence.**
  `workflow_integration_test.TestOperatorReferenceMatchesReality.
  test_the_happy_path_does_not_route_an_approve_through_the_revise_command`.

---

## I12 — two unwritten phases documented as live gates (reviewer `A1`, `A5`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

- **Description.** The reference's "Phases" section listed all 17
  `KNOWN_PHASES` names as one undifferentiated set, its happy path showed
  `AWAITING_TECHNICAL_APPROVAL` as a `★ gate` an item transitions into,
  its next-command table had a row keyed on that phase, and
  `/apply-functional-review`'s section said the command "enters
  `FIXING_FUNCTIONAL_FINDINGS`". An operator checking
  `work_items[<id>].phase` against any of those would find something else
  and have no way to tell which of the two was wrong.

- **Proof** (mechanical, two independent derivations, both now standing
  tests): an AST walk of every `phase` assignment in
  `scripts/workflow_state.py`
  (`workflow_state_test.TestPersistedPhaseWriterCensus`) and a line scan of
  the same module
  (`workflow_integration_test._phase_writing_state_functions`) agree that
  13 of the 17 declared phases are written, by these writers:
  `default_work_item`, `publish_plan_revision`,
  `transition_to_awaiting_local_plan_review`, `record_local_plan_review`,
  `record_manual_plan_review`, `apply_plan_approval`,
  `complete_checkpoint`, `enter_self_reviewing_implementation`,
  `record_bundle_generation`, `enter_applying_review_feedback`,
  `apply_technical_approval`, `promote_legacy_work_item`,
  `import_legacy_work_item`, `complete_work_item`. The four with no
  writer are `SELF_REVIEWING_PLAN`, `AWAITING_TECHNICAL_APPROVAL`,
  `FIXING_FUNCTIONAL_FINDINGS` and `AWAITING_USER_ACCEPTANCE`.

  This is `O1`'s finding — recorded there as `ACCEPTED`, because the
  *runtime* is not wrong to leave them unwritten. What is new here is that
  the operator-facing documents presented two of them as live, which `O1`
  never covered.

- **Root cause.** `AWAITING_PLAN_APPROVAL` and `AWAITING_TECHNICAL_APPROVAL`
  look symmetric — both have a `*_gate_reachable` predicate — but only the
  first also has a writer (`record_manual_plan_review`). The documents
  generalized from the plan stage to the implementation stage.

- **Fix.** The "Phases" section is now two tables: persisted phases with
  their writers, and declared-but-unwritten names with what each one
  actually is. The happy path, the next-command table and
  `/apply-functional-review`'s and `/approve-review`'s sections were
  rewritten to match, and the hard-gate paragraph now says which two of the
  six gate names are not phases.

- **Regression evidence.**
  `TestOperatorReferenceMatchesReality.test_the_persisted_phase_table_is_exactly_the_writer_census`
  and `test_every_persisted_phase_row_names_its_real_writer` compare the
  document's own tables against the derived census, so a new writer forces
  the document to move; plus
  `test_apply_functional_review_is_not_documented_as_entering_an_unwritten_phase`.

---

## I13 — the completion-obligations refusal was undocumented (reviewer `A6`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `complete_work_item` raises
  `UnsatisfiedCompletionObligationError` when any obligation the item's own
  registry declares does not derive `PASS` — independently of, and in
  addition to, the incomplete-child and own-checkpoint blocks. Neither
  `.claude/commands/accept-milestone.md` step 2a (which listed exactly
  three stops and said "none of these three") nor the operator reference's
  `/accept-milestone` **Refuses** list mentioned it. An operator hitting
  the repository's own release gate had no documented next step.

- **Reproduction.** Against the live repository:
  `resolve_completion_obligations(repo_root, work_items["workflow-v2-1-core"])`
  returns `WFO-LEDGER-COVERAGE` and `WFO-STATE-SERIALIZATION`, both
  `VERIFIER_UNRESOLVABLE` at the current `HEAD` — i.e. a non-`PASS`
  classification that would refuse acceptance for any non-terminal item
  declaring them. (That item is `MILESTONE_COMPLETE`, so the refusal is
  inert for it today; see `O17` for why its verdicts are unresolvable.)

- **Fix.** `accept-milestone.md` step 2a documents the stop, what to report
  (obligation id, classification, and for a `FAIL` the failing conformance
  assertions), that a registry declaring none passes vacuously, and that it
  is a gate to fix rather than retry past — "none of these **four** stops".
  The operator reference's **Refuses** list names it, the two live
  obligation ids, and its independence from the other blocks.

- **Regression evidence.**
  `TestWorkItemTargetingContract.test_accept_milestone_documents_the_completion_obligation_refusal`
  and
  `TestOperatorReferenceMatchesReality.test_accept_milestone_refusals_include_the_completion_obligations`,
  the latter deriving the obligation ids from
  `ws.COMPLETION_OBLIGATION_CONFORMANCE` rather than restating them.

- **Fix commit.** `4866a2a` (command) and the operator-reference commit
  that follows it.

---

## O15 — four false operator-reference claims (reviewer `A3`, `A4`, `A7`, `A8`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

Each was checked against the artifact it describes, not against another
document:

- **`A3` — "The only command with `state_writer: false`".** Three command
  files declare it: `prepare-review.md`, `review-implementation.md`,
  `review-functional.md` (derived from frontmatter by
  `_declared_state_writer_false`). `/review-plan` is *not* one of them — it
  records a ledger stage and a phase — which is worth stating, since the
  three "review" commands otherwise look interchangeable.
- **`A4` — reviewer-role casing.** `LOCAL_MODEL_PLAN_REVIEW` and
  `MANUAL_EXTERNAL_PLAN_REVIEW` are the canonical constants;
  `_normalize_plan_review_stage_key` maps the lowercase spellings *to*
  them as legacy compatibility values, and
  `validate_manual_plan_review_preconditions` accepts either. The
  reference said the feedback file "must declare `Reviewer role:
  manual_external_plan_review` **exactly**" — wrong on the canonical
  spelling and on "exactly". Both stage names and the `/review-plan`
  output are now canonical, with the legacy tolerance stated once.
- **`A7` — the provenance claim.** Known discrepancy 3 asserted that
  `MILESTONE_WORKFLOW.md` *and* `PLAN_REVIEW_WORKFLOW.md` document
  `Reviewer role:`. `PLAN_REVIEW_WORKFLOW.md` does not contain the string
  at all (it does discuss `review_content_id`). The discrepancy is
  corrected in place and says which document actually covers what; the
  historical documents themselves are untouched.
- **`A8` — "not hand-maintained".**
  `test_the_operator_reference_command_count_matches_reality` derives both
  the `### /<name>` section set and the on-disk stem set and asserts
  **equality** — that half is genuinely derived. The number 15 is a
  hardcoded literal in the same test (`assertEqual(len(on_disk), 15)`), a
  deliberate tripwire, not a derivation. The paragraph now says exactly
  that, and keeps the tripwire rather than removing inventory validation.

- **Regression evidence.** One test each in
  `TestOperatorReferenceMatchesReality`
  (`test_the_state_writer_false_claim_matches_the_frontmatter`,
  `test_the_canonical_reviewer_roles_are_presented_as_canonical`,
  `test_the_plan_review_workflow_provenance_claim_is_true`,
  `test_the_command_inventory_claim_states_the_real_guarantee`), each
  deriving its expectation from the artifact under description.

---

## O16 — `/bootstrap-workflow-v2`'s retirement condition is met but unexecuted

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  BASELINE_V2_3_0 · **Status.** ACCEPTED

- **Description.** `bootstrap-workflow-v2.md` states of itself: "It is
  retired — deleted — only at this work item's own `MILESTONE_COMPLETE`."
  `work_items["workflow-v2-1-core"].phase` is `MILESTONE_COMPLETE`. The
  file is still on disk, still in the operator reference's roster of 15,
  and still in `_GOLDEN_COMMAND_FILE_SHA256`.

- **Observed consequence.** None that is reachable. The command's step 2
  durability guard calls `implementing_entry_reachable`, which for that
  item raises `UnclassifiedPathError` before returning anything (see
  `O17`), and its `publish_plan_revision` call would raise
  `TerminalPlanRevisionPublicationError` on a terminal item. Every path
  through it fails closed.

- **Why ACCEPTED rather than fixed here.** Retiring a command is a scoped
  act with its own surface — `I10`'s retirement of
  `/accept-scoped-remediation` needed a dedicated conformance class, an
  operator-reference roster change, a count-tripwire change, dual-mode
  enumeration edits and a golden-hash update. Doing it as a side effect of
  a convergence pass would be exactly the expansion this campaign is meant
  to avoid, and nothing about the current state is unsafe. Recorded, with
  the operator reference now saying plainly that the file still exists and
  that its target is terminal, rather than asserting a deletion that has
  not happened.

---

## O17 — `workflow-v2-1-core`'s declarations do not classify two later documents

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  BASELINE_V2_3_0 · **Status.** ACCEPTED

- **Description.** Recomputing `workflow-v2-1-core`'s identity at the
  current `HEAD` raises
  `UnclassifiedPathError('docs/ai-workflow/WORKFLOW_V2_3_1_PLAN.md')` at
  the implementation stage and
  `UnclassifiedPathError('docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md')`
  at the plan stage. Both files were written after that work item
  completed, and `workflow-v2-1-core-artifacts.json` — frozen by its own
  approval — names neither. This is why its two completion obligations
  read `VERIFIER_UNRESOLVABLE` (see `I13`).

- **Why this is the fail-closed default working, not a hole.** The
  classifier deliberately cannot distinguish "deliberately out of scope"
  from "nobody thought about it", so an unnamed path refuses rather than
  being silently ignored (`D-Fingerprint-Generalization`, `B5`/`B7`).

- **Why ACCEPTED.** The item is `MILESTONE_COMPLETE`. Its own declaration
  is plan-stage protected content bound to an approval that can never be
  refreshed, and `REVIEW_PROTOCOL.md`'s "Repairing an artifact declaration
  after an approval" requires carrying such a repair back through that
  stage's gate — impossible, and pointless, for a terminal item. The
  active work item `workflow-v2-3-1` was checked directly and is
  unaffected: its plan-stage `review_content_id` and implementation-stage
  identity both recompute cleanly, and it declares no completion
  obligations. Recorded so a future reader does not mistake the
  `VERIFIER_UNRESOLVABLE` verdicts for a live release-gate failure.

---

## I14 — the lifecycle diagram misstated four runtime contracts (reviewer `B1`, `B2`, `B3`, `B6`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

The diagram is the first thing an operator reads and the only place several
of these facts appear together. Each claim below was checked against the
function it describes.

- **`B1` — the remediation-child note said the child "Runs the FULL
  lifecycle above (`AWAITING_EXTERNAL_PLAN_REVIEW` → ... →
  `MILESTONE_COMPLETE`)".** There is no remediation-specific entry rule:
  `create_remediation_child_work_item` produces an ordinary work item at
  `PLANNING` whose `governing_workflow_version` is the config default at
  creation, and `publish_plan_revision`'s own version branch then decides
  — `AWAITING_LOCAL_PLAN_REVIEW` for `"2.1"` (this repository's default),
  `AWAITING_EXTERNAL_PLAN_REVIEW` only for `"1"`. Bound by
  `workflow_state_test.TestRemediationChildWorkItem.
  test_child_enters_the_plan_review_stage_its_own_governing_version_selects`,
  which exercises both branches, and asserted at the acceptance-matrix
  level by row `C7`.
- **`B2` — the `SELF_REVIEWING_IMPLEMENTATION` note credited
  `enter_self_reviewing_implementation` as "the same writer the ordinary
  last-checkpoint case uses".** It is not. `complete_checkpoint` writes
  that phase when the checkpoint it is completing turns out to be the last
  one; `enter_self_reviewing_implementation` is the second, distinct
  writer, added by ledger `B8` for the case where every checkpoint is
  *already* `COMPLETE` (a plan re-approval). Both are now named for what
  they own.
- **`B3` — `recovered` was drawn as a third `record_bundle_generation`
  outcome.** The function raises
  `InvalidBundleGenerationOutcomeError` for anything but `"ordinary"` and
  `"same_content"`; provenance recovery is a separate operation
  (`apply_implementation_provenance_recovery`, driven by
  `/recover-implementation-provenance`) that writes a different field set
  entirely (`RECOVERED_BUNDLE_GENERATION_RECORD_FIELDS`). Generation and
  recovery are now stated as different things.
- **`B6` — implementation-review `BLOCK` was absent while the plan lane
  represented `BLOCK` explicitly.** It is supported, durable and
  consequential, and is *not* a phase transition:
  `/apply-implementation-review` step 1 calls
  `record_technical_review_block_pin` against the exact `bundle_id` before
  triaging anything, then remediates exactly as for `REVISE`;
  `technical_approval_gate_reachable` afterwards refuses that `bundle_id`
  regardless of what the mutable feedback file is later edited to say
  (`GPT-R55-002`). Only a newly generated bundle can be approved. Drawn as
  a note, deliberately not as an edge — there is no transition to draw.

- **Regression evidence.**
  `workflow_integration_test.TestLifecycleDiagramMatchesTheCode`, four
  tests, each cross-checking the diagram's own rendered text against the
  live function (e.g. the outcome test reads
  `inspect.getsource(ws.record_bundle_generation)`).

---

## I15 — the diagram did not distinguish declared from persisted phases (reviewer `B4`, `B5`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

- **Description.** Of the four phases nothing writes (`O1`, and `I12` for
  the operator-reference half), the diagram gave `AWAITING_TECHNICAL_APPROVAL`
  and `AWAITING_USER_ACCEPTANCE` their own explanatory notes — worded
  correctly, but styled exactly like every other note, so nothing
  distinguished "this is not a state" from ordinary commentary — and did
  not mention `SELF_REVIEWING_PLAN` or `FIXING_FUNCTIONAL_FINDINGS` at
  all. A reader could not derive the four-name set from the diagram.

- **Fix.** One consolidated note carrying all four, in a visually distinct
  style (dotted purple border, its own `◇ DECLARED, NEVER WRITTEN`
  heading) that appears nowhere else in the diagram, replacing the two
  scattered notes. Each name is given with what it actually is.

- **Regression evidence.**
  `TestLifecycleDiagramMatchesTheCode.test_the_unwritten_phases_are_named_only_in_their_own_marked_note`
  requires all four in that note and none of them anywhere else, and
  `test_every_phase_the_diagram_draws_as_a_box_has_a_writer` derives the
  written set from `workflow_state.py` and requires every solid phase box
  to be in it.

---

## O18 — the diagram's rendering (reviewer `C1`-`C6`, plus a full layout audit)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

The file carries the diagram twice — the drawio `mxfile` in the
`<svg content="...">` attribute and hand-authored SVG primitives in the
body — and nothing checked either the two against each other or the result
against a renderer. Every item below was confirmed by rendering the
committed SVG with `rsvg-convert` and looking at it.

- **`C1`** — the `LEGACY_READY` note (`25,836 270x53`) overlapped
  `/approve-review plan` *and* completely covered the
  `▶ IMPLEMENTING — continues in lane 2` box (`25,871 270x27`) and the
  arrow into it. A live box was invisible. Moved to `25,930`.
- **`C2`** — the bounded functional-fix return ran at `x=996`, inside the
  note column's own `800..1030` band, so most of it was painted over.
  Both long returns now share one visible channel in a dedicated gutter
  and merge, since they are the same transition from two sources.
- **`C3`** — `e12` and `e27` were the same two points; `e27` carried the
  label. `e12` removed.
- **`C4`** — `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW → /review-implementation`
  and `AWAITING_FUNCTIONAL_REVIEW → /prepare-functional-review` had no
  arrow at all. Both added; the second required moving the section-3
  caption out of its channel.
- **`C5`** — `regenerated post-fix bundle` overlapped the
  bundle-outcomes note, `no code change` sat on a box edge, and the local
  `REVISE` label was printed inside the `/review-plan` box. All labels are
  now clear of every box and of each other. The same pass found and fixed
  three more: the `clean` bypass ran straight through the
  remediation-child note; the manual-`REVISE` route ran hidden behind
  three notes; and the `AWAITING_USER_ACCEPTANCE` note's first line
  overflowed its own box by 13px.
- **`C6`** — the manual external `REVISE` pointed directly at
  `/apply-plan-review`, skipping `REVISING_PLAN`, which
  `record_manual_plan_review` really does write. Both `REVISE` routes now
  reach `REVISING_PLAN`; they share one channel and merge, so the state is
  entered once, from both verdicts.

- **Layout.** The three columns were too tight to route any of this
  legibly — the gap between lane 1 and the middle column was 15px and the
  gap before lane 2 was 5px. The canvas widens from 1055 to 1230 to give
  each inter-lane gutter a real channel. Nothing moved vertically except
  the notes that had to.

- **Two edge crossings remain, deliberately.** Three edges leave lane 2
  leftwards at interleaved heights (`clean` bypassing
  `/apply-functional-review`, `broad` to the child note, `no code change`
  back to the gate), so a crossing is topologically forced. Both survivors
  are between a solid grey edge and a dashed red one, never two of the
  same kind — the red-on-red crossing the first repair pass introduced was
  found by the audit and rerouted away.

- **Regression evidence.**
  `workflow_integration_test.TestLifecycleDiagramMatchesTheModelItCarries`
  (4 tests: the model and the body must declare the same boxes and the
  same polylines, in both directions, and no edge may be duplicated) and
  `TestLifecycleDiagramLayout` (5 tests: no box overlap, no edge segment
  under a box, no label over a box, no crossing outside the documented
  pair, nothing outside the canvas). Ten of the sixteen diagram tests fail
  against the pre-fix file.

---

## O19 — `/prepare-functional-review` credited with guards it does not have

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

- **Found by** the re-audit's per-command cross-check: every phase or
  status name in an operator-reference **Expects** line must appear in the
  command file it describes. `technical_approval.status == CURRENT` did
  not appear in `prepare-functional-review.md` at all.

- **What the command really does.** Step 0 resolves a target; step 0a
  performs `LEGACY_READY` adoption if that target is the dormant item;
  steps 1-5 confirm verification, write the checklist, and make the
  checklist-evidence commit. There is no phase guard and no
  `technical_approval` check anywhere outside the adoption branch. The
  reference also said it "enters `AWAITING_FUNCTIONAL_REVIEW`", which
  `/approve-review implementation` has already done by then — the only
  phase this command ever writes is on the adoption path, where
  `promote_legacy_work_item` writes exactly that phase.

- **Why not repaired in the command.** This is the same non-guarding shape
  ledger `O7` records for `/milestone-implement` and `O13` for
  `complete_work_item`, and the same disposition applies: no gate
  downstream is weakened, because `/review-functional` and
  `/accept-milestone` both check the phase themselves
  (`milestone_complete_gate_reachable`). Running it early produces a
  checklist and an evidence commit for an unapproved round and nothing
  more. Adding a guard would be hardening beyond this campaign's scope;
  the reference now says plainly that there is none.

- **Regression evidence.**
  `TestOperatorReferenceMatchesReality.test_prepare_functional_review_is_not_credited_with_guards_it_lacks`,
  which asserts the absence in the command file and the corrected wording
  in the reference together.

---

## O20 — `AWAITING_PLAN_APPROVAL` shown as the `"1"` path's next state

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

- **Found by** re-running the phase-writer census against the reference's
  per-version claims after `I12`'s repair. `record_manual_plan_review` is
  the **only** writer of `AWAITING_PLAN_APPROVAL`, and it calls
  `_require_v2_1_plan_review`, which refuses any item whose
  `governing_workflow_version` is not `"2.1"`. So a `"1"`-governed item
  can never occupy that phase — yet the reference read
  "`AWAITING_EXTERNAL_PLAN_REVIEW` → `/apply-plan-review` →
  `AWAITING_PLAN_APPROVAL`" for exactly that version, and
  `/apply-plan-review`'s **Next** line said `"1"` →
  `AWAITING_PLAN_APPROVAL`.

- **What really happens.** `/apply-plan-review` step 7's `"1"` branch
  *reports* `AWAITING_PLAN_APPROVAL` as the next gate and writes no phase.
  The item stays at `AWAITING_EXTERNAL_PLAN_REVIEW` until
  `/approve-review plan` writes `IMPLEMENTING`. This is the same
  gate-name-versus-phase distinction `I12` fixed for
  `AWAITING_TECHNICAL_APPROVAL`, one version narrower: the gate is a
  persisted phase for `"2.1"` and a name only for `"1"`. Nothing in the
  runtime is wrong — `plan_approval_gate_reachable` takes no phase
  argument and `apply_plan_approval` has no source-phase guard, so the
  approval works from `AWAITING_EXTERNAL_PLAN_REVIEW` exactly as intended.

- **Regression evidence.**
  `TestOperatorReferenceMatchesReality.test_the_v1_plan_approval_gate_is_not_described_as_a_phase`,
  deriving the refusal from `inspect.getsource(ws._require_v2_1_plan_review)`.

---

## O21 — the remediation-child completion branch edited a shared file in place

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Found by** re-reading `B9`'s own repair adversarially: the new
  `/accept-milestone` step 2b said that a remediation child's completion
  should be recorded "in the **parent's** own functional-review checklist
  in `docs/ACTIVE_MILESTONE.md`, against the deferral note
  `/apply-functional-review`'s broad branch wrote there".

- **Why that is wrong.** `workflow_state.FUNCTIONAL_CHECKLIST_PATH` is one
  shared file for every work item (ledger `D2` disproved that hardcoded
  path as a defect in itself). By the time the child is accepted, its own
  `/prepare-functional-review <child-id>` has already overwritten that
  file with the *child's* checklist, so there is no parent deferral note
  left there to edit. Directly observable in acceptance-matrix row `C7`,
  whose real sequence is: child's checklist → child accepted → **parent
  re-runs `/prepare-functional-review`** → parent accepted.

- **Blast radius, measured.** Checklist *evidence* is a commit — a blob
  plus a `Workflow-Functional-Checklist:` trailer, discovered from history
  by `discover_current_functional_checklist_evidence` — never the working
  tree, so nothing durable is lost when the file is overwritten. The two
  real consequences while a child is open are that
  `/review-functional <parent-id>` reports the parent's checklist as
  drifted from its own evidence blob (its live-blob comparison is doing
  exactly its job), and that the parent needs a fresh
  `/prepare-functional-review <parent-id>` round afterwards. Neither
  weakens a gate: `/accept-milestone` never reads the checklist.

- **Fix.** Step 2b now reports rather than edits — it names
  `/prepare-functional-review <parent-id>` as the parent's next action and
  says why an in-place edit is wrong. The operator reference's
  remediation-children section states the shared-file consequence
  explicitly, including the expected `/review-functional` drift report.

---

## O22 — "Enter the `X` state" is not a phase write, and `REVISING_PLAN` is version-scoped

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

- **Found by** the clean-slate pass asking a blunt question: which
  transitions does the *command contract* claim, and which does the code
  perform? Ten command files open with an `Enter ...` line naming a phase.
  Cross-checking each against whether that file actually calls a writer of
  it gives three different meanings for one sentence:

  | Meaning | Commands |
  |---|---|
  | really writes it | `/accept-milestone`, `/apply-implementation-review`, `/prepare-functional-review` (adoption branch only) |
  | names the phase it runs **in** | `/milestone-plan`, `/review-plan`, `/milestone-implement`, `/apply-plan-review`, `/record-manual-plan-review` |
  | names a **gate**, not a phase | `/approve-review`, `/apply-functional-review` |

  The first cut of this repair matched only the narrow "Enter the `X`
  state" phrasing and so accounted for eight of the ten, silently missing
  `/approve-review` — whose preamble names `AWAITING_TECHNICAL_APPROVAL`,
  the very phase this campaign spent `I12` establishing is never written —
  and `/record-manual-plan-review`, whose "Enter the **exit of** `X`" is
  in fact the one precise phrasing in the set. Caught by the final
  clean-slate pass re-deriving the preamble set with `^Enter ` rather than
  the narrower pattern, which is now what the test does.

  This is the root of the reviewer's `A5` and of half of `A1`: the
  reference read those opening lines as transitions, because there was
  nothing saying they are not.

- **Second half.** `/apply-plan-review`'s preamble names `REVISING_PLAN`
  and the file calls no writer of it — and neither does anything else for
  a `"1"` item, since both real writers
  (`record_local_plan_review`/`record_manual_plan_review`) go through
  `_require_v2_1_plan_review`. So `REVISING_PLAN` carries exactly the
  version scoping `O20` found on `AWAITING_PLAN_APPROVAL`, and a
  `"1"`-governed item's whole plan lane persists three phases:
  `PLANNING` → `AWAITING_EXTERNAL_PLAN_REVIEW` → `IMPLEMENTING`.

- **Fix, deliberately narrow.** The eight command files are **not**
  rewritten — the wording is an inherited v1 convention, changing it in
  eight executable contracts is expansion, and nothing about it is unsafe
  once it is understood. The operator reference gains the three-way table
  above, the instruction to read `work_items[<id>].phase` rather than a
  command's opening line, and the `(2.1)` marking on `REVISING_PLAN`.

- **Regression evidence.**
  `TestOperatorReferenceMatchesReality.test_the_enter_the_state_preamble_convention_is_documented_correctly`
  derives the eight preambles and the writer set from source and requires
  the reference to account for them, and
  `test_the_v1_plan_lane_persists_only_three_phases` derives the version
  guard from `inspect.getsource`.

---

## O23 — the reference named a helper that does not exist

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Found by** the clean-slate pass resolving every backticked identifier
  in `WORKFLOW_V2_1_OPERATOR_REFERENCE.md` against `workflow_state` and
  `workflow_fingerprint`. Fifty-nine names, one unresolved:
  `normalize_plan_review_stage_keys`, written into the reviewer-role
  paragraph by this campaign's own `O15` repair. The real read-site helper
  is `normalize_plan_review_stages`; `migrate_plan_review_stage_keys` is
  the separate one-time migration. Corrected to name both, for what each
  does.

- **Regression evidence.**
  `TestOperatorReferenceMatchesReality.test_every_code_symbol_the_reference_names_actually_exists`,
  which resolves every such name and keeps its own data-name allowlist
  honest by asserting that nothing on it has since become a symbol.

---

## I16 — the first plan bundle resolved to the wrong directory (reviewer `I1`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `.claude/commands/milestone-plan.md` step 6 authors
  `<bundle_dir>/REVIEW_REQUEST.md`, `<bundle_dir>/TEST_RESULTS.md` and
  `<bundle_dir>/CONTEXT_FILES.txt`, and only then runs
  `./scripts/prepare-ai-review.sh <base-sha> plan <work_item_id>`.
  `<bundle_dir>` is `workflow_fingerprint.resolve_bundle_dir`'s answer,
  which was `.ai-review/<work_item_id>/current` **if that directory
  already exists**, else the flat `.ai-review/current`. For a work item
  with no scoped directory yet the two halves of one command therefore
  disagreed: the author wrote to `.ai-review/current/`, while the
  generator's own `ROOT_DIR` (and
  `write_manifest_with_verified_identifiers_for_work_item`, which already
  templated the scoped path *by direct templating, explicitly to avoid
  this resolver*) wrote and validated `.ai-review/<work_item_id>/current/`.

- **Reproduction**, driving the real resolver and the real generation
  script against disposable scratch repositories, four scenarios, all
  four failing on the first attempt before the repair:

  | Scenario | `resolve_bundle_dir` answered | generator refused with |
  |---|---|---|
  | new `process` item, first plan bundle | `.ai-review/current` | `MissingReviewContentIdStatementError: .../.ai-review/proc-item/current/REVIEW_REQUEST.md` |
  | new `product` item, first plan bundle | `.ai-review/current` | same, at `.../.ai-review/milestone-9/current/REVIEW_REQUEST.md` |
  | withdraw an existing bundle, then regenerate | `.ai-review/current` | same |
  | remediation child, first `/milestone-plan <child-id>` | `.ai-review/current` | same, at `.../.ai-review/milestone-9-remediation-1/current/REVIEW_REQUEST.md` |

  A second, byte-identical attempt then succeeded — because the *failed*
  first generation had created the scoped directory the resolver
  afterwards preferred. That is what made this survivable in practice and
  invisible in review: the operator experienced it as "run it twice."

- **Root cause.** The existence gate is a *proxy* for "was this work item
  generated with a `work-item-id`", and the proxy has no way to be right
  before the first generation. `REVIEW_PROTOCOL.md` already stated the
  correct contract in prose — "For the **plan stage specifically**, the
  bundle directory is never the flat fallback ... always resolve to
  `.ai-review/<work_item_id>/current/` **by construction**" — and both
  generator-side path producers implemented it. Only the resolver the
  command docs name for the *authoring* half never learned it.

- **Fix, stage-aware rather than global.** The flat fallback is not
  obsolete: `prepare-ai-review.sh`'s `work-item-id` argument is optional
  for `implementation`, `post-fix` and `functional-review`, and omitting
  it is a documented, supported invocation that really does write
  `.ai-review/current/` — making every stage scoped by construction would
  break it the mirror-image way. So `resolve_bundle_dir` gained a `stage`
  keyword: `stage="plan"` returns the scoped path with no gate at all,
  every other stage keeps the compatibility rule, and an unrecognized
  stage raises `InvalidBundleStageError` rather than falling through to
  the compatibility branch on a typo. `resolve_feedback_dir` is
  deliberately untouched — `feedback/` is stage-agnostic by contract
  (`REVIEW_PROTOCOL.md`, `REQ-21`).

  The five plan-stage command files, `prepare-review.md`'s plan branch,
  the two plan-stage call sites inside `workflow_fingerprint.py`
  (`resolve_plan_stage_approval_commit_paths`, the CLI's plan-stage
  inspection) and `REVIEW_PROTOCOL.md` all now name the argument.
  `approve-review.md` runs at both stages and states the stage-dependent
  form.

- **Regression evidence.**
  `workflow_acceptance_matrix_test.py`'s
  `FirstAttemptPlanBundleResolution` — four scenarios, each invoking
  `prepare-ai-review.sh` exactly **once** and asserting that one
  invocation succeeded, so a regression cannot hide behind a second
  attempt — plus
  `workflow_fingerprint_test.py`'s `TestBundleLayoutResolverStageAwareness`.

## I17 — the acceptance matrix bypassed the resolver it claimed to drive (reviewer `I2`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `workflow_acceptance_matrix_test.py`'s own header
  promises "the exact `workflow_state`/`workflow_fingerprint` entry points
  the `.claude/commands/*.md` files name — never a private
  reimplementation of a command's logic". `Item.bundle_dir` was one:

  ```python
  def bundle_dir(self):
      return self.root / ".ai-review" / self.wid / "current"
  ```

  It never called `resolve_bundle_dir`, and `generate_plan_bundle` then
  `mkdir(parents=True, exist_ok=True)`'d that hardcoded path *before*
  authoring — which both bypassed the resolver and pre-created the exact
  directory whose absence is `I16`'s trigger. `Item.feedback_dir` did the
  same to `resolve_feedback_dir`. Every row claiming command-level
  generation, regeneration or recovery — `A1`, `A4`, `A5`, `B5`, `B6`,
  `C7`, `E8`, `F1`, `F2`, `G1` — was therefore asserting against a private
  substitute for the one function that was wrong.

- **Root cause, and why it is the same root cause as ledger `I5`.** This
  is the campaign's own recurring failure mode: a test that stands in for
  a command dependency encodes the substitute as the specification. `I5`
  named it for `WORKFLOW_STATE.json` writes; this is the path-resolution
  instance of it, in the suite created *by* that repair.

- **Fix.** `Item.bundle_dir(stage=None)` and `Item.feedback_dir()` now
  return `resolve_bundle_dir`/`resolve_feedback_dir`'s answers, and
  `bundle_dir` deliberately creates nothing — creating the directory is
  what made the hardcoded version answer correctly by side effect. `stage`
  is passed exactly where the owning command passes it: `"plan"` at the
  twelve plan-stage sites, omitted at the implementation/post-fix sites
  whose commands document the compatibility rule.

- **Control arms**, run against the repaired sources with exactly one
  repair's hunks reverted (`scripts/` copied nowhere — patched in place
  from a committed tree and restored):

  | Arm | Matrix result |
  |---|---|
  | pre-repair resolver, `Item.bundle_dir` routed through it | **73 failures** |
  | pre-repair resolver, hardcoded `Item.bundle_dir` restored | **6 failures** — only the rows added by this pass |
  | repaired resolver, hardcoded `Item.bundle_dir` restored | **green** |

  The third arm is the finding, stated exactly: with the substitute in
  place the matrix is green whatever the resolver does. The 6-versus-73
  contrast is how much the substitute was hiding — 67 assertions in
  pre-existing rows became sensitive to `resolve_bundle_dir` only because
  `Item.bundle_dir` now calls it.

## I18 — the same split at the implementation stage, after any withdrawal

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Found by** the re-audit `I16`/`I17` required of the closure around
  `resolve_bundle_dir`/`withdraw_bundle`/regeneration — not reported by
  the reviewer.

- **Description.** `withdraw_bundle` renames `current/` to a
  `current.rejected-<token>/` sibling. A work item that had been
  generating scoped bundles for rounds therefore had *no*
  `.ai-review/<id>/current/` immediately afterwards, so the existence gate
  answered flat for its next round — while `prepare-ai-review.sh`, given
  the same `[work_item_id]` argument as the round before, still wrote
  `.ai-review/<id>/current/`. `/milestone-implement` authored
  `IMPLEMENTATION_SUMMARY.md` at the flat path, the generator's own empty
  stub was what `assert_stage_completeness` read, and the regenerated
  bundle was withdrawn too.

- **Reproduction.** Row `B6`'s own scenario carried one step further:
  after the stale-summary withdrawal, `resolve_bundle_dir(root, wid)`
  answered `.ai-review/current` where the script writes
  `.ai-review/proc-item/current` — `DISAGREE`, printed side by side.

- **Fix.** `stage="plan"` cannot help here: the flat layout is genuinely
  reachable at these stages. What is *not* a legitimate signal is
  `current/`'s existence, since a withdrawal is defined to remove it. Both
  `resolve_bundle_dir` and (by derivation) `resolve_rejected_marker_path`
  now decide a work item's layout from its **own root directory**,
  `.ai-review/<work_item_id>/`, which a withdrawal does not touch. A work
  item that never had a scoped directory created still resolves flat, so
  the genuinely flat-generated legacy layout is unchanged.

- **Regression evidence.** Row `B6` now regenerates after its own
  withdrawal and requires first-attempt success, plus
  `test_non_plan_stages_stay_scoped_while_current_is_quarantined` and
  `test_a_never_scoped_work_item_still_resolves_flat_at_non_plan_stages`.

## O24 — the plan-approval takeover never checked whose transaction it was (reviewer optional finding 3)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** The plan-approval journal is a single,
  repository-wide object (`.ai-review/runtime/PLAN_APPROVAL_JOURNAL.json`),
  not a per-work-item one. `/approve-review B plan` step 4b calls
  `plan_approval_takeover_evidence(repo_root)`, which observes whatever
  transaction is open — including one belonging to work item A — and the
  recovery path then resumes forward-completion or rollback at step 6a
  from `journal[...]`: A's pinned record, A's applicable paths, A's
  expected post-state, under B's invocation. Nothing compared
  `journal["work_item_id"]` to the command's resolved target.

- **Reproduction.** A journal opened for `proc-item`, then
  `take_over_plan_approval_transaction` invoked while targeting
  `milestone-9`: **succeeded**, rotating the owner token. The
  authorization literal does name `proc-item` — but naming A is not the
  same as checking that the operator meant A, and the literal is quoted
  back from the evidence the command itself just printed.

- **Fix, deliberately narrow.** `work_item_id` becomes a **required**
  keyword argument of `take_over_plan_approval_transaction`, refused with
  `PlanApprovalTakeoverWorkItemMismatchError` (a subclass of the existing
  `PlanApprovalTakeoverRefusedError` — the same refusal, at the same
  point, not a new takeover mode) at step 1a, before the claim, the guard
  and the rotation, having mutated nothing. Required rather than optional
  precisely because an optional cross-check leaves the
  forget-to-pass-it hole this pass exists to close. Takeover semantics are
  otherwise untouched. `approve-review.md` step 4b states the check.

- **Regression evidence.**
  `workflow_integration_test.py`'s
  `test_takeover_refuses_a_journal_belonging_to_a_different_work_item`
  (which also asserts nothing was mutated: same owner token, `takeover_count`
  still `0`, no `PLAN_APPROVAL_JOURNAL.claim.*` left behind, and the
  rightful target can still take it over) and
  `test_takeover_work_item_mismatch_is_a_refusal_subclass`.

## O25 — the withdrawal crash window left a marker every consumer read past (reviewer optional finding 4)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `withdraw_bundle` writes the `REJECTED` marker *before*
  the first removal, deliberately, so it is visible at every instant of
  the sequence; it removes it only once every step has completed. Between
  the successful `os.rename` of `current/` and `marker_path.unlink()`
  there is a window. `resolve_rejected_marker_path` derived its answer
  from `resolve_bundle_dir`, which was keyed on `current/`'s existence —
  so inside that window the marker sat at `.ai-review/<id>/REJECTED` while
  every fresh resolution answered `.ai-review/REJECTED`.

- **Reproduction.** Simulated the exact window (rename landed, unlink did
  not): `resolve_rejected_marker_path` answered `.ai-review/REJECTED`,
  `assert_bundle_not_rejected` **passed**. Fail-open, on precisely the
  residue the marker exists to refuse — the reviewer's "purely cosmetic
  with all consumers fail-closed" escape did not hold.

- **Fix.** Nothing of its own: `I18`'s change to the layout rule both
  resolvers share is what closes it, and
  `resolve_rejected_marker_path` stays the one-line derivation from
  `resolve_bundle_dir` rather than growing a second copy of that rule.
  No transaction machinery, no new crash-window handling.

- **Regression evidence.**
  `test_marker_resolves_scoped_while_current_is_quarantined`,
  `test_clearing_the_marker_targets_the_same_resolved_path` (the residue is
  cleared by the next successful generation rather than becoming sticky),
  `test_a_work_item_with_no_scoped_root_still_resolves_flat`, and
  `test_marker_path_is_derived_from_the_bundle_resolver_not_a_second_copy`.

## I19 — `/apply-functional-review`'s bounded fix names none of its generation preconditions (reviewer Important finding)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `.claude/commands/apply-functional-review.md`'s
  bounded-code-change branch (step 4, sub-item 4) drives a `post-fix`
  bundle generation. It named the `REJECTED` refusal, the outcome
  resolution, `record_bundle_generation`, the durability commit and its
  trailers — and then said, verbatim, "Then run
  `./scripts/prepare-ai-review.sh <base-sha> post-fix [work_item_id]`".
  It never defined `<bundle_dir>` (the preamble resolved
  `<feedback_dir>` alone), never mentioned `REVIEW_REQUEST.md`'s
  `review_content_id: <hex>` line, and never mentioned
  `IMPLEMENTATION_SUMMARY.md`'s `implementation_revision: <N>` line.
  Both are hard generator preconditions with *different* failure modes,
  and neither is ever silently corrected.

- **Reproduction** (disposable scratch repository carrying a real copy of
  the tooling; the item driven to `AWAITING_FUNCTIONAL_REVIEW` through
  the real commands, then the bounded branch executed exactly as written,
  leaving the previous round's bundle inputs in place because the command
  named no authoring step):

  - **attempt 1** — exit 1,
    `ReviewContentIdMismatchError: .../REVIEW_REQUEST.md states
    review_content_id='9949f0cb…', manifest computes 'ec1c4085…'`, raised
    from `--write-manifest` →
    `write_manifest_with_verified_identifiers_implementation_stage` →
    `assert_review_request_states_review_content_id`. A **refusal**:
    `current/` intact, nothing published, nothing withdrawn;
  - **attempt 2**, with only that line corrected — exit 1,
    `status: withdrawn`,
    `IMPLEMENTATION_SUMMARY.md states implementation_revision: 1, the
    work item's current counter is 2`, from
    `finalize_bundle_generation` → `assert_stage_completeness`. A
    **withdrawal**: `current/` renamed to
    `current.rejected-<token>/`, `review-bundle.tar.gz` deleted. (The
    `REJECTED` marker is absent afterwards because a *completed*
    withdrawal removes its own marker by design — the quarantine
    directory is the evidence, not the marker.);
  - **attempt 3**, after re-authoring all four inputs the quarantine took
    with it — exit 0, published.

  **Control arm, same command text, `same_content` outcome:** exit 0 on
  the first attempt with **nothing** re-authored. That is why the gap
  survived: `record_bundle_generation(outcome="same_content")` pins
  `implementation_revision` by contract, and the round's protected
  implementation-stage content is byte-identical by definition, so the
  previous round's `review_content_id` recomputes to the same digest.
  Acceptance-matrix row `C3` was exercising exactly that branch.

- **Why the suites were green.** Two independent reasons, both closed:
  1. `workflow_integration_test.py`'s
     `TestGenerationCommandsNameTheCompleteAuthorInputSet` named "the
     four commands that drive a generation". Eight do. The one omitted
     from the four was the one with the gap;
  2. `workflow_acceptance_matrix_test.py`'s `Item.write_bundle_inputs`
     refreshed **both** lines for every generation, including the bounded
     functional fix — authoring, on the command's behalf, what the
     command never told an operator to author. Rows `C2`/`C3`/`C8`
     therefore passed over a contract no operator following the command
     could have satisfied.

- **Fix, command text only.** The preamble now resolves
  `<bundle_dir>`/`<feedback_dir>` through
  `workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir`, and
  states that this command's only generation is a `post-fix` round, so
  `<bundle_dir>` takes the compatibility form
  (`resolve_bundle_dir(repo_root, work_item_id)`, no `stage` argument),
  never the plan stage's scoped-by-construction one. Step 4's bounded
  branch gains the same generation-authoring contract
  `/apply-implementation-review` step 7 already carries — refresh
  `IMPLEMENTATION_SUMMARY.md`'s `implementation_revision: <N>` (advanced
  for `"ordinary"`, deliberately *unchanged* for `"same_content"`),
  refresh `REVIEW_REQUEST.md`'s `review_content_id: <hex>` — each naming
  the assertion that enforces it and the consequence, with the
  withdrawal-versus-refusal distinction and the reproduction's own
  two-step failure order stated explicitly. No Python, no shell, no
  lifecycle semantics changed.

- **Regression evidence.**
  - `workflow_integration_test.py`: the census is now **discovered** from
    the command corpus (`_GENERATOR_RUN_RE`) and compared against a
    declared roster, with every generator-mentioning command required to
    be classified as either a driver or mention-only —
    `test_the_declared_census_is_exactly_the_commands_that_run_the_generator`,
    `test_every_command_mentioning_the_generator_is_classified`,
    `test_no_mention_only_command_instructs_its_own_generation`;
    per-driver obligations via the pure checker
    `_generation_authoring_violations`
    (`test_every_generation_driving_command_states_its_full_authoring_contract`,
    `test_apply_functional_review_names_the_bounded_fix_generation_contract`,
    `test_the_pinned_round_driver_states_why_it_refreshes_nothing`,
    `test_the_ad_hoc_and_bootstrap_drivers_route_authoring_through_the_protocol`).
  - **The mutation regression**,
    `TestRevertingTheAuthoringInstructionsFailsConformance`: the repair's
    own two hunks are reverted mechanically out of the *current* file and
    the same checker is run against the result, which must report every
    precondition by name. The mutation asserts it actually changed
    something first, so a reworded hunk can never degrade it into a
    no-op that "passes". Executed on disk as well as in memory: with the
    file reverted, `workflow_integration_test.py` goes from 236 OK to 6
    failures.
  - `workflow_acceptance_matrix_test.py` rows `C9` and `C10`: the
    behavioural halves, driving the bounded branch with `refresh_inputs=
    False` and pinning both failure modes in order, and the
    `same_content` control that legitimately needs neither refresh.
    `Item.write_bundle_inputs` now documents that every line it writes is
    a line the owning command's own step names.

## O26 — the acceptance matrix's control-arm figures did not describe the suite (reviewer optional finding 2)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** Three separate errors in one table. The columns were
  "Rows that fail"/"Rows that stay green" and never summed to the suite:
  the "green" column silently included the 16 skipped rows. Some figures
  were `unittest`'s `failures=`/`errors=` summary counts, which expand a
  row's sub-tests into separate entries, mixed into a column labelled
  *rows*. And the `I17` arm's surviving-failure set was described as
  "only the rows pass 10 added", when one of the six is `B6`, a
  pre-existing implementation-lifecycle row that pass 10 made
  resolver-sensitive rather than added.

- **Reproduction.** Every arm rebuilt from the current sources with
  exactly one repair's hunks reverted and re-run. Arm 5 (`I18`'s
  pre-repair resolver) reports `failures=6` in `unittest`'s summary and
  fails **3** rows; the `I17` arm's six survivors are `I1`-`I5` plus
  `B6`.

- **Fix.** Documentation only. The execution record now states the
  counting rule, lists each arm's mutation as a reproducible recipe, and
  carries `Fail`/`Pass`/`Skip` columns that sum to 97 in every arm.

## O27 — the flat compatibility path had a resolver assertion but no generation (reviewer optional finding 3)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `resolve_bundle_dir` preserves the flat
  `.ai-review/current/` layout for the three stages whose
  `[work-item-id]` argument is optional, and matrix row `I5` asserted
  exactly that — the resolver's *answer*. Nothing anywhere executed a
  successful generation through the omitted-id form, so "the authoring
  half and the generating half agree at the flat path" was an untested
  claim on both sides of the very split ledger `I16`/`I18` were about.

- **Fix.** Matrix row `I6`
  (`test_a_flat_compatibility_generation_succeeds_first_attempt`):
  `<bundle_dir>` resolved through the production resolver, the inputs
  authored there, `prepare-ai-review.sh` run with the id omitted, and
  that one invocation required to publish — for `implementation`,
  `post-fix` and `functional-review` alike, with the scoped layout
  asserted never to be created. No compatibility semantics were
  broadened: the flat form still writes no implementation-stage
  `MANIFEST.md` (the script writes one only in its `-n "$WORK_ITEM_ID"`
  branch), so no finalization runs, exactly as `/review-implementation`
  step 3 already reports.

## O28 — `/bootstrap-workflow-v2` (reviewer optional finding 4)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** SINGLE_ITEM · **Status.** ACCEPTED

- **Description.** `/bootstrap-workflow-v2` is a one-time driver with a
  hardcoded `workflow-v2-1-core` target. In isolation it can reproduce
  the first-generation split, since its step 3 authors
  `<bundle_dir>/...` and then runs
  `prepare-ai-review.sh <base_commit> implementation workflow-v2-1-core`.

- **Why accepted.** Its target work item is complete, so the command is
  unreachable at current `HEAD`, and every guard it shares with the other
  drivers fails closed rather than publishing. It is nonetheless carried
  in the census as a genuine generation driver
  (`_GENERATION_DRIVING_COMMANDS`) and covered by
  `test_the_ad_hoc_and_bootstrap_drivers_route_authoring_through_the_protocol`,
  so it cannot fall out of the population unnoticed. Not repaired and not
  redesigned in this pass; revisit only on evidence of a reachable
  supported-path impact. This restates `O16`'s own accepted disposition
  (its stated retirement condition is met but was never executed) rather
  than opening a second one.

## I20 — finalization was gated on a stale file, so a stage with no identity withdrew another round's bundle (reviewer `N1`)

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction.** `scripts/workflow_acceptance_matrix_test.py`,
  `CrossStageGenerationReuse` / `...Product` (rows `J1`-`J8`). Before the
  fix, driving the real `scripts/prepare-ai-review.sh` at stage
  `functional-review` over a published scoped bundle produced, for a
  `process` item, a `product` item, and over implementation-, post-fix-
  and plan-published bundles alike:

  - exit status 1, `status: withdrawn`, `bundle_id mismatch: manifest=…
    ondisk=… extracted=…`;
  - `.ai-review/<id>/current/` gone, renamed to a
    `current.rejected-<token>/` sibling;
  - `.ai-review/<id>/review-bundle.tar.gz` deleted;
  - `MANIFEST.md` deleted;
  - `docs/ai-workflow/WORKFLOW_STATE.json` **byte-identical** (this script
    is not a state writer), so `technical_approval` stayed `CURRENT` and
    the phase stayed `AWAITING_FUNCTIONAL_REVIEW` over a bundle that no
    longer existed;
  - and the completed withdrawal removing its own `REJECTED` marker on
    the way out (correct by design, ledger `D1`), leaving
    `assert_bundle_not_rejected` with nothing to see.

- **Root cause.** The closing check was gated on
  `[[ -f "$BUNDLE_DIR/MANIFEST.md" ]]` — a *filesystem* test — while two
  of the script's four stages write no manifest at all
  (`functional-review` at either layout, and `implementation`/`post-fix`
  with the work-item-id omitted). `$BUNDLE_DIR` is reused across stages by
  design, so a `functional-review` run found the previous round's manifest
  and ran the three-way `bundle_id` check against it. That check could
  only ever fail: `CHANGED_FILES.txt` alone carries a `stage:` line the
  run has just rewritten, so the recomputed identifier disagrees with the
  recorded one by construction. The failure path is `withdraw_bundle`.

- **Fix.** `a4d871c`. Finalization is gated on `$MANIFEST_WRITTEN`, this
  invocation's own producer state, set only on the far side of a
  `--write-manifest` call that returned 0. A run that generated an
  identity must prove that identity reproduces; a run that generated none
  has nothing to prove and no standing to withdraw another round's
  artifact. The stale manifest is left in place — deleting it to suppress
  the check would be the same filesystem-state reasoning inverted, and
  would destroy the published round's own identity record — and a
  non-manifest stage now reports that what it inherited no longer
  describes the directory or the archive (`O30`).

- **Regression evidence.** Rows `J1`-`J3` (both types): not withdrawn, not
  quarantined, archive present, state and technical approval unchanged,
  functional-review outputs generated into both the directory and the
  archive, previous manifest byte-identical; `J1` continues to acceptance,
  so the round is proven still *usable*, not merely still present. Row
  `J4`: a genuine post-fix round afterwards writes its own manifest and
  finalizes it, with manifest / on-disk / archived `bundle_id` agreeing
  again. Row `J5`: the control — a stale `IMPLEMENTATION_SUMMARY.md`
  counter at a stage that does write a manifest still fails closed
  (withdrawn, quarantined, archive gone). Row `J6`: its mirror — the same
  stale line at the non-manifest stage withdraws nothing.

- **Mutation control.** Restoring the `-f "$BUNDLE_DIR/MANIFEST.md"` gate
  fails **14 of the 16** `J` rows; the two survivors are exactly `J5`
  (both types), whose withdrawal is correct in both worlds.

## I21 — the technical-approval commit's own validator had no production caller

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction.** `TechnicalApprovalCommitValidation` /
  `...Product`. A technical-approval commit that also adds a second work
  item's entry *and* changes the top-level `active_work_item_id` is
  accepted by `discover_technical_approval_commit`, by
  `verify_post_approval_manifest_match`, and by the completion-obligation
  verifier — while `validate_technical_approval_commit`, which exists
  precisely to refuse it, names the offending work item exactly.

- **Root cause.** `validate_technical_approval_commit` is implemented,
  documented and unit-tested, and no `.claude/commands/*.md` file and no
  production function calls it: **zero** production call sites. Its own
  docstring calls it "the sibling `validate_bundle_generation_record_commit`
  already provides for generation-record commits" — and that sibling has
  two live call sites, inside the provenance-interval walk
  `/approve-review implementation` already reaches. The plan stage has
  step 6a's post-commit verification set; the implementation stage had
  none. This is `OPUS-R133-003`'s class one approval stage over.

- **Fix.** `5206ea9`. `/approve-review` step 6's implementation-stage
  paragraph names the stage's own post-commit verification set — both
  `validate_technical_approval_commit` and
  `verify_post_approval_manifest_match` — with the failure disposition
  stated (stop, report, human resolution; never silently amend).

- **Deliberately forward-only.** Applied to the commit the invocation just
  created, never retroactively to discovered history. Two
  technical-approval commits already in this repository do not satisfy the
  contract: `9fd3c72` (`v2-1-dry-run`) recorded the approval without a
  `phase` transition, and `ae51770` (`workflow-v2-1-core`) has a wider
  field set. History rewriting is not available — such SHAs are pinned
  into `WORKFLOW_STATE.json` and approved bundles — so wiring the check
  into `discover_technical_approval_commit` would have refused real,
  already-approved rounds. Both SHAs are named in the command text and
  asserted by conformance, so the scope is a recorded fact rather than an
  unexplained choice.

- **Regression evidence.** Eight rows across both types: a commit touching
  another work item, one recording the approval without the phase
  transition, and one changing a top-level routing field are each refused
  by name; the control proves the ordinary sequence every other row drives
  satisfies both checks, so the repair constrains malformed commits
  without refusing real ones. Plus a conformance row requiring the command
  to name both checks, the forward-only scope and the two SHAs.

## I22 — `/apply-plan-review` regenerates the registry and leaves the plan document's table behind it

- **Severity.** IMPORTANT · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction.** `PlanDocumentRegistryTableFreshness` / `...Product`,
  row `test_the_pre_repair_sequence_publishes_a_table_the_registry_contradicts`.
  Following step 5 exactly, a revision that adds `CP2` leaves the plan
  document declaring only `CP1` while the registry declares both — and the
  plan bundle publishes cleanly.

- **Root cause.** `/milestone-plan` step 3 calls the plan document's
  checkpoint table its own "generated, human-readable checkpoint table —
  never hand-edited", produced by `render_registry_markdown`.
  `/apply-plan-review` step 5 regenerates `registry_path`/`mapping_path`
  at the new `plan_revision` and never said to refresh it. Nothing detects
  the result: the table is not hashed separately (the whole document's
  bytes are, so a stale table simply *is* what `review_content_id`
  covers), and `assert_stage_completeness` checks only the `(Revision N)`
  marker. The external reviewer reviews the stale table as the plan.

- **Why it survived.** This suite's own `Item.apply_plan_review` helper
  called `render_registry_markdown` all along — a step no operator
  following the command would have performed — so every plan-revision row
  stayed green over the gap. Found by the mechanical helper-vs-command
  audit, not by inspection.

- **Fix.** `5206ea9`. Step 5 names the re-embed, unconditionally: the
  render is a pure function of the checkpoint set, so it is a
  byte-identical no-op when nothing moved, and a conditional step only
  reinvites the judgement call that produced the staleness. Conformance:
  a plan-marker-step driver that names `generate_registry` without
  `render_registry_markdown` is a violation, with a live-file mutation
  control arm.

- **Considered and not done.** A production check
  (`render_registry_markdown(registry) in plan_document`) is feasible —
  three of the four live work items already satisfy it exactly. The
  fourth, `workflow-v2-1-core`, does not; it is the terminal, permanently
  `"1"`-governed bootstrap item. Adding a new refusal was judged
  disproportionate for this pass and is recorded here so a later review
  can weigh it on its own.

## O29 — a second hand-maintained census behind the mechanical one (reviewer `N2`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction.** A synthetic command text running
  `./scripts/prepare-ai-review.sh <base-sha> post-fix [work_item_id]` and
  naming `resolve_bundle_dir` — nothing else. It matches the run regex, so
  the discovery test forces it into `_GENERATION_DRIVING_COMMANDS`; and
  `_generation_authoring_violations` returns `[]` for it, before *and*
  after adding it to the main census, because the marker-line checks were
  gated on membership of a second literal, `_MOVING_COUNTER_DRIVERS`.

- **Root cause.** Pass 11 made the *population* mechanical and left the
  *obligations* hand-maintained one level down. The same hole existed on
  the plan side: the two live plan drivers' contracts were asserted by two
  hand-written tests naming those two files, so a third plan driver would
  have been checked by nothing.

- **Fix.** `56ac637`. The run regex captures the stage token out of each
  command's own invocation line. Every driver owes a declared discipline
  (`_GENERATION_AUTHORING_CONTRACTS`) and silence is a violation, not an
  exemption. The declared `kind` is checked against the derived stage, so
  a `post-fix` driver cannot conform by claiming the plan stage's
  different contract; the census's stage column is checked against the
  file rather than trusted; and both exemption kinds
  (`protocol-reference`, `pinned-round`) carry evidence checks against the
  command text. No command file changed: every live driver already
  satisfied the contract its stage owes.

- **Control arms.** A planted `post-fix` driver with no contract fails; one
  declaring `moving-counter-step` at a silent step fails naming every
  missing line; one claiming either exemption unearned fails naming the
  missing evidence; a planted *plan* driver fails the same way; and a
  driver declaring a contract its stage does not owe fails as inadmissible.
  The planted text is passed to the checker directly rather than written
  into `.claude/commands/`, so the live corpus other rows read is never
  perturbed.

## O30 — a non-manifest stage inherits the previous round's manifest

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** ACCEPTED

- **Description.** `I20`'s repair leaves the previous round's
  `MANIFEST.md` in `current/` when a `functional-review` generation runs
  over it, and the run re-archives the directory. The manifest's declared
  `bundle_id` therefore no longer describes either the directory or the
  archive.

- **Why accepted, with evidence.** Deleting it would destroy the published
  round's own identity record and would be the same filesystem-state
  reasoning inverted — the exact thing `I20` repaired. Every live consumer
  **recomputes** `bundle_id` rather than reading the declared value
  (`/approve-review` step 2, `/review-plan` step 5,
  `/review-implementation` step 4), so the residual is fail-closed at
  every approval gate rather than silently accepted: row `J8` drives it —
  feedback naming the pre-regeneration identity no longer matches the
  recomputation and `assert_feedback_matches_bundle` refuses. Row `J4`
  proves the next genuine generation restores consistency. And the run now
  says so on `stderr` rather than leaving an operator to discover it when
  a reviewer's own recomputation disagrees (row `J7`).

## O31 — the `review_content_id` an author must state had no named computation (reviewer `N3`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction.** Mechanical: for each of the five in-gate generation
  drivers, the step block that requires `REVIEW_REQUEST.md` to state
  `review_content_id: <hex>` was searched for any of
  `compute_review_content_id_plan_stage_for_work_item`,
  `approval_review_content_id`,
  `compute_review_content_id_implementation_stage_at_commit`,
  `load_implementation_stage_classification`. **None** named any of them.
  `assert_review_request_states_review_content_id` *refuses* the whole
  generation on a wrong value, so this was an instruction with no
  procedure — the author had to pick among seven entry points, with up to
  eight arguments, at an unstated anchor.

- **Fix.** `b5e448c`. `docs/ai-workflow/REVIEW_PROTOCOL.md` gains one
  "Computing `review_content_id`" section naming exactly one existing
  entry point per stage, the commit-source anchor, and the two cautions
  `/review-implementation` step 4 already carried in long form. The
  algorithm is restated nowhere: each recipe is a call into the single
  existing entry point. The five drivers, plus `/review-plan` and
  `/record-manual-plan-review` (which named no computation either), carry
  a reference to the section. Naming it is now a mechanical conformance
  obligation for both step-form disciplines.

- **Regression evidence.** The recipes are *executed*, not asserted: four
  rows per work-item type run each documented recipe with the documented
  arguments and require the result to equal the value the generator's own
  `--write-manifest` step independently recorded — including both
  cautions, shown load-bearing (the worktree-source variant diverges the
  moment the tree is dirty; the wrong `artifacts_path` computes another
  item's classification). Control arm: a wrap-tolerant regex strips the
  pointer out of each live driver in memory and the checker names the loss
  for all five.

## O32 — the implementation/post-fix given-id split on a fresh tree (reviewer `N4`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** ACCEPTED

- **Reproduction.**
  `FirstAttemptPlanBundleResolution.test_the_given_id_split_on_a_fresh_tree_is_fail_closed_and_self_healing`.
  Reachable only when `.ai-review/<work_item_id>/` is genuinely absent,
  which past the plan approval means the gitignored,
  documented-disposable `.ai-review/` tree was deleted between rounds —
  probed directly and confirmed that the normal tracked flow and a
  post-`withdraw_bundle` regeneration both keep the scoped root, so
  neither reaches it.

- **Why accepted.** Refusal, not withdrawal:
  `assert_review_request_states_review_content_id` names the scoped
  `REVIEW_REQUEST.md`, nothing is published, no marker is written, and the
  bundle authored at the flat path survives untouched. The failed attempt
  creates the scoped directory, so an identical second attempt — the same
  command text, re-followed, with no extra step — succeeds. The row
  asserts all four properties including the successful retry. Closing it
  by changing the resolver would break the omitted-id compatibility form
  the existence gate exists for, and no supported invocation is blocked,
  only delayed by one retry. Documented in `REVIEW_PROTOCOL.md` beside its
  plan-stage twin.

## O33 — `/bootstrap-workflow-v2` past its retirement condition (reviewer `N5`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  SINGLE_ITEM · **Status.** ACCEPTED

- **Description.** The command's own text says it "is retired — deleted —
  only at this work item's own `MILESTONE_COMPLETE`", and
  `workflow-v2-1-core` reached `MILESTONE_COMPLETE`. It is left in place.

- **What changed this pass.** Its unreachability was asserted in a source
  comment in the census and executed nowhere — a claim, not evidence,
  which is the substitution class this campaign audits for.
  `workflow_state_demo_test.py`'s
  `test_the_bootstrap_driver_is_unreachable_and_fail_closed_at_live_head`
  now reads the live state file and proves the command is unreachable
  *and* fail-closed: terminal by phase and by its own registry,
  `select_next_checkpoint` selects nothing, and both writers its terminal
  branch would call refuse this phase by name
  (`IllegalSelfReviewEntryPhaseError`,
  `IllegalBundleGenerationSourcePhaseError`). Supersedes `O28`'s and
  `O16`'s narrative disposition with an executed one; not repaired, not
  revived, not redesigned.

## O34 — four documented phases no writer persists (reviewer `N6`)

- **Severity.** OPTIONAL · **Confidence.** PROVEN_STATIC_CLOSURE ·
  **Scope.** GENERIC_DESIGN · **Status.** FIXED

- **Description.** `MILESTONE_WORKFLOW.md` carried a full state-reference
  section for `SELF_REVIEWING_PLAN`, `AWAITING_TECHNICAL_APPROVAL`,
  `FIXING_FUNCTIONAL_FINDINGS` and `AWAITING_USER_ACCEPTANCE` — four
  phases no writer ever persists — reading exactly like the thirteen that
  are persisted. The document promised resumable states no live state file
  can contain. This is `O1`'s finding, previously `ACCEPTED` with no
  documentation change; pass 12 takes the small documentation-only fix the
  reviewer's `N6` asks for.

- **Fix.** `48d4221`. Each section is marked, naming the writer that
  actually moves the item, and a "Vocabulary states" paragraph explains
  that `KNOWN_PHASES` is deliberately a union of the v1 and v2.1
  vocabularies — never-persisted is not the same as invalid, so a
  historical state file carrying one still validates. No historical
  approved artifact is rewritten.

- **Regression evidence.** The set is held true mechanically rather than
  by prose: `TestNeverPersistedPhaseVocabulary` derives the persisted set
  from `workflow_state.py`'s own AST (both the literal assignment shape
  and `publish_plan_revision`'s `target_phase` indirection, which is the
  only way `AWAITING_EXTERNAL_PLAN_REVIEW` is reachable), asserts both
  halves partition `KNOWN_PHASES`, and binds the document's markers to the
  derived set. A phase that gains a writer — or a fifth that loses one —
  fails there rather than leaving the note quietly wrong.

## O37 — the fourth author-written generation precondition was undocumented

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Reproduction.**
  `CanonicalReviewContentIdRecipe.test_a_foreign_bundle_id_line_refuses_the_generation`.
  A `REVIEW_REQUEST.md` written to the "Review request format" section's
  own list, plus a `bundle_id: <64 hex>` line, fails the whole generation:
  `ForeignBundleIdFieldError('REVIEW_REQUEST.md', [5])`.

- **How it was found.** Enumerating everything `--write-manifest` and
  `finalize_bundle_generation` require of *author-written* files, after
  `O31` mechanised the three that were already known
  (`assert_review_request_states_review_content_id`,
  `assert_stage_completeness`,
  `assert_test_results_consistent_with_plan_review_request`). This is the
  fourth, and the only *prohibition* among them — which is why every
  earlier census missed it: they were all looking for required lines.

- **Why it matters.** It is an easy line to write by accident: the
  feedback protocol asks the reviewer to quote the bundle id back
  (`Reviewed bundle ID:`), so stating it in the request that produced the
  bundle reads as helpful. Nothing in `REVIEW_PROTOCOL.md` or any command
  said not to.

- **Fix.** `REVIEW_PROTOCOL.md`'s "Author-written files" section states
  the prohibition, why `MANIFEST.md` is the single schema-defined
  location, that `<feedback_dir>` is outside the bundle and never hashed,
  and the remedy (state it in prose, not in that labelled form); the
  "Review request format" list carries the cross-reference.

- **Why Optional rather than Important.** Fail-closed at the strongest
  point: the generation refuses, `current/` is intact, nothing is
  published and nothing is withdrawn, and the exception names the file and
  the line numbers. Removing the line is the whole remedy — the row
  asserts the corrected generation publishes on its first attempt.

- **Noted and deliberately not changed.** The same check applies to
  `files/` copies and `DIFF.patch`, which are *generated* from repository
  content: a tracked file containing a line exactly `bundle_id: <64 hex>`
  would, once it entered a diff, fail the generation closed. A repository
  scan (`git grep -nE '^bundle_id: [0-9a-f]{64}$'`) returns **no** tracked
  file today, so the condition is unreachable at this `HEAD`; exempting
  `files/` would be a change to identity computation, which this pass does
  not make on an unreachable path. Recorded here for a later reviewer to
  weigh.

## O36 — the helper-vs-command audit itself was a one-off

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `I21`, `I22` and `O35` were all found by one mechanical
  sweep run by hand: extract every `workflow_state`/`workflow_fingerprint`
  entry point each acceptance-matrix helper calls, and check the owning
  command's own text names it. A sweep that runs once finds today's gaps
  and prevents none of tomorrow's — which is the same shape as the census
  finding it produced (`O29`), one level up.

- **Fix.** `TestMatrixHelpersDoNotOutrunTheirCommands` in
  `workflow_integration_test.py`. The owner mapping is keyed on `Item`'s
  own existing section comments and the discovered set must equal the
  declared set, so a new helper section cannot slip in unaudited and a
  declared one that no longer exists cannot rot. Calls are read from the
  AST and restricted to actual invocations — an exception class named in
  an `except` clause is a reaction to a call, not a step a command
  instructs. Two discharges are recorded with reasons
  (`compute_review_content_id_plan_stage_for_work_item`, discharged by
  `O31`'s protocol reference; `assert_feedback_matches_bundle`, which
  `/approve-review` step 2 deliberately does not assert), each checked
  rather than trusted, and a third row requires the discharge list to stay
  minimal and used so a stale exemption cannot sit ready to excuse the
  next genuine gap.

- **Control arm.** Stripping `render_registry_markdown` out of
  `/apply-plan-review`, and `validate_technical_approval_commit` out of
  `/approve-review`, in memory from the live files, each makes the checker
  report exactly that call — so the audit is evidence, not an assertion
  nobody has watched fail.

## O35 — the matrix's own helper made an approval basis unreachable

- **Severity.** OPTIONAL · **Confidence.** PROVEN_EXECUTION · **Scope.**
  GENERIC_DESIGN · **Status.** FIXED

- **Description.** `resolve_approval_basis` has two outcomes and the
  string `USER_OVERRIDE` appeared nowhere in the acceptance matrix.
  `Item.approve_implementation` called `assert_feedback_matches_bundle`
  unconditionally — a step `/approve-review` step 2 explicitly does *not*
  instruct ("not fatal to reading the file (an `EXTERNAL_APPROVE` basis
  simply becomes unreachable, per step 3), but report the mismatch naming
  both values") — so every row reaching an implementation approval
  necessarily had matching feedback. The override half was unexecuted end
  to end, including the interim remedy
  `/recover-implementation-provenance` step 7 names by that exact word.

- **Fix.** `e722ed5`. The helper mirrors the command: the binding check is
  observed and reported, and `resolve_approval_basis` makes the decision;
  `expect_basis` pins which branch a row intends. Eight rows across both
  types: stale-bundle feedback resolving `USER_OVERRIDE` and completing
  through acceptance; a `REVISE` verdict reaching the same basis; matching
  feedback still earning `EXTERNAL_APPROVE` with no mismatch reported (the
  control that the relaxed helper did not relax the decision); and `BLOCK`
  reaching neither basis at both enforcement points — the gate, which is
  what actually fires in the command's own ordering, including on
  `pinned_block` alone after D2a's laundering path, and
  `resolve_approval_basis`'s independent second layer asserted directly
  because the gate means it is never reached.
