# Workflow v2.x — Acceptance Matrix

The end-to-end scenarios the repaired system must prove. Every row is
executed by `scripts/workflow_acceptance_matrix_test.py` against a
disposable scratch repository that carries a real copy of the tooling,
driving the **real** `scripts/prepare-ai-review.sh` as a subprocess and
the exact `workflow_state`/`workflow_fingerprint` entry points the
`.claude/commands/*.md` files name — never a private reimplementation of
a command's logic.

Run it with:

```bash
python3 scripts/workflow_acceptance_matrix_test.py
```

It is hermetic and stdlib-only, depends on no fixed historical base
commit and on none of this repository's own live `WORKFLOW_STATE.json`,
and runs in CI alongside the other conformance suites.

**Both work-item types.** Rows marked **P+P** run twice — once for a
`process` item (deliverable prefix `scripts/`) and once for a `product`
item (deliverable prefix `app/`, the type no end-to-end fixture had ever
exercised). Rows marked **P** are type-independent and run once, on the
process item; the product subclass skips them by name rather than
silently omitting them.

**Fidelity rules the suite holds itself to**, because breaking either is
what let the ledger's defects survive:

- the four plan-stage files stay **uncommitted and intent-to-add** until
  `/approve-review plan`'s own commit, exactly as a real work item does,
  so every row exercises the conditional fifth commit member;
- checkpoint work runs through `/milestone-implement`'s real ownership
  machinery (`resolve_checkpoint_ownership`, `claim_checkpoint`,
  `owner_mutation`, `committed_checkpoint_status`, `release_checkpoint`),
  not a direct state write;
- no row passes an `artifacts=` override, so every row depends on
  `generate_artifacts_declarations`' real output;
- **no row writes a `phase` value into `WORKFLOW_STATE.json` by hand**
  (added in convergence pass 7, ledger `O9`). Every
  `stage="implementation"` generation routes through
  `Item.milestone_implement_wrap_up`, which performs
  `/milestone-implement`'s real step-1a/1b/1c sequence and its step-2
  writer, so no row can reach a first-round bundle without the real
  transition having happened. The one row that forged it —
  pre-repair `C5` — is exactly the row that hid ledger `B8` for a
  whole campaign;
- **no row resolves a bundle or feedback path by hand** (added in
  convergence pass 10, ledger `I17`). `Item.bundle_dir`/`Item.feedback_dir`
  call `workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir` —
  passing `stage` exactly where the owning command passes it, and creating
  nothing, since pre-creating the scoped directory is what made the old
  hardcoded helper answer correctly by side effect. The hardcoded helper is
  exactly what let ledger `I16` reach a fresh independent review with the
  matrix green: with a substitute in place, no row can observe the
  resolver at all.

---

## A. Plan lifecycle

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| A1 | Fresh plan → local APPROVE → manual APPROVE → plan-approval commit | P+P | plan-stage generation, the two-stage ledger, the full journal/guard/staging/commit/classify/verify/materialize transaction, `verify_post_approval_manifest_match`, and the approval commit's exact five-member set | `test_a1_fresh_plan_to_approval` |
| A2 | Plan REVISE (local) → revise → regenerate → APPROVE | P+P | `record_local_plan_review` REVISE, `transition_to_awaiting_local_plan_review`, revision publication, regeneration at a new revision | `test_a2_plan_revise_local` |
| A3 | Multiple plan REVISE rounds, including a manual-external REVISE | P+P | no path re-enters manual review without a fresh local pass; the ledger's own round numbers | `test_a3_plan_revise_multiple_rounds` |
| A4 | Plan-stage generation with a stale `TEST_RESULTS.md` | P | the withdrawal path fires, quarantines, and a clean regeneration afterwards publishes (`I3`) — and, since pass 10, that the regeneration resolves its own bundle directory through the real resolver rather than a hardcoded path (`I16`, `I17`) | `test_a4_plan_stage_stale_test_results_withdraws` |
| A5 | Plan-stage files left unstaged | P | the tracked-path refusal, and that the staging step is the whole remedy (`B6`) | `test_a5_unstaged_plan_files_refuse_before_writing_anything` |

## B. Implementation lifecycle — all three generation modes

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| B1 | Round 1 → APPROVE → technical approval | P+P | ordinary generation, the manifest's two head fields naming two different commits (`I1`), the provenance interval, `technical_approval_gate_reachable`, `validate_technical_approval_commit` | `test_b1_implementation_first_round_approve` |
| B2 | REVISE → ordinary fix → round 2 → REVISE → round 3 → APPROVE | P+P | repeated ordinary rounds, revision advance, `enter_applying_review_feedback` | `test_b2_implementation_revise_rounds` |
| B3 | REVISE → **excluded-only** fix → `same_content` round | P+P | `("same_content", t)`, the recovered-role commit, and that regeneration **succeeds** and the round is approvable (`B1`) | `test_b3_same_content_round` |
| B4 | Two consecutive `same_content` rounds | P+P | supersession-chain continuity across more than one link | `test_b4_same_content_chain` |
| B5 | Excluded-only commit past `T` → `/recover-implementation-provenance` | P+P | the recovery precondition, the `S2` commit, step 6's regeneration succeeding, and recovery becoming a no-op afterwards (`B2`) | `test_b5_provenance_recovery` |
| B6 | Generation with a stale `IMPLEMENTATION_SUMMARY.md`, then regeneration | P | withdrawal fires (`I4`), and the round after it succeeds on its **first** attempt — the implementation-stage half of the post-withdrawal misresolution (`I18`) | `test_b6_implementation_stale_summary_withdraws` |
| B7 | Idempotent regeneration at the same head and stage | P | byte-identical reproduction; no spurious revision bump | `test_b7_idempotent_regeneration` |
| B8 | Revision jumped by two; moved backwards; bumped at the same head | P | the monotonicity rule still refuses all three | `test_b8_revision_monotonicity_refusals` |
| B9 | New head with no generation-record commit | P | the provenance-interval half still refuses — `R1` did not weaken it | `test_b9_unrecorded_new_head_refused` |
| B10 | Protected content changed, revision left alone, chain forged | P | `ProtectedPathInProvenanceIntervalError` still refuses | `test_b10_protected_change_without_revision_refused` |
| B11 | Two `same_content` links, then a genuine change | P | the round advances to revision 2 and starts a fresh ordinary-role chain | `test_b11_same_content_chain_then_an_ordinary_round` |
| B12 | `same_content` republication, then `/recover-implementation-provenance` | P | both recovered-role producers in one chain stay continuous, and the pinned head survives into `technical_approval.reviewed_content_commit` | `test_b12_same_content_round_then_provenance_recovery` |
| B13 | Plan revised and re-approved mid-implementation | P | `O7`: uncommitted vs committed staleness, the refusal once committed, containment at `/accept-milestone`, and the completed checkpoints surviving a fresh approval | `test_b13_plan_revised_and_re_approved_mid_implementation` |
| B14 | Plan re-approved with **every** checkpoint already `COMPLETE` | P | ledger `B8`: the terminal `IMPLEMENTING` wedge reached through the real lifecycle, all four outgoing refusals, `enter_self_reviewing_implementation` as the real transition, its no-op on re-entry, and the `same_content` republication and acceptance that follow | `test_b14_plan_re_approved_with_every_checkpoint_already_complete` |

## C. Functional lifecycle and acceptance

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| C1 | Technical approval → checklist evidence → clean → `/accept-milestone` | P+P | evidence-trailer discovery, `complete_work_item`'s blocks, the terminal transition and `active_work_item_id` reset | `test_c1_functional_clean_to_acceptance` |
| C2 | Functional REVISE → bounded fix → post-fix → fresh review → re-approval → accept | P+P | `mark_technical_approval_stale` ordering, `record_bundle_generation` from `AWAITING_FUNCTIONAL_REVIEW`, the consumed marker, a second technical approval | `test_c2_functional_bounded_fix_cycle` |
| C3 | Functional bounded fix that nets out byte-identical | P | `B3`'s third manifestation: `same_content` from `AWAITING_FUNCTIONAL_REVIEW` publishes | `test_c3_functional_same_content_fix` |
| C4 | Functional review consumed twice | P | `assert_functional_review_not_already_consumed` refuses the replay, and fresh content is readable again | `test_c4_functional_review_consumed_guard` |
| C5 | An outstanding checkpoint cannot shortcut the self-review gate | P | ledger `O9`, repaired with ledger `B8`: the row that used to **hand-write** `SELF_REVIEWING_IMPLEMENTATION` now asserts the writer refuses by name with the outstanding checkpoint attached, that ownership resolves a concrete checkpoint rather than `NO_CHECKPOINT`, that generation refuses from `IMPLEMENTING` — then runs the honest tail to `MILESTONE_COMPLETE` | `test_c5_outstanding_checkpoint_cannot_shortcut_the_self_review_gate` |
| C6 | Broad remediation deferred to a child work item | P | deterministic child id, inherited type/kind, `parent_work_item_id`, focus never stolen, the parent blocked while the child is open. Clearing that block is deliberately **not** exercised here -- it needs the child's real cycle, which is `C7`; this row used to forge `phase: MILESTONE_COMPLETE` onto the child instead (ledger `B9`) | `test_c6_broad_remediation_child` |
| C7 | A remediation child running its own full cycle | P | `/milestone-plan <child-id>` fills in the `null` `mapping_path` through the resume branch and creates the declarations file the child was born without; the child enters at `AWAITING_LOCAL_PLAN_REVIEW` for its own `"2.1"` governing version, survives a plan-review `REVISE` round driven through `/apply-plan-review <child-id>` while the parent still holds `active_work_item_id`, implements, is approved and accepted; the parent is blocked right up to `/accept-milestone <child-id>` and completes only afterwards | `test_c7_remediation_child_runs_its_own_cycle` |
| C8 | Two functional rounds with an identical checklist | P | `I7`: each round gets its own evidence commit, and every downstream check accepts the empty-commit form | `test_c8_unchanged_checklist_still_gets_its_own_round_evidence` |
| C9 | Bounded functional fix regenerated **without** refreshing either author-written line | P | ledger `I19`, in the order the checks really fire: `assert_review_request_states_review_content_id` *refuses* the generation from `--write-manifest` (nothing published, nothing withdrawn, `current/` intact); with only that corrected, `assert_stage_completeness` *withdraws* the bundle from `finalize_bundle_generation` (quarantine sibling, archive deleted, and a completed withdrawal removes its own `REJECTED` marker); with both refreshed the same generation publishes first-attempt, and the round is still approvable | `test_c9_bounded_fix_without_refreshed_author_inputs` |
| C10 | The same bounded branch resolving `same_content`, nothing refreshed | P | why `I19` survived a green matrix: a pinned round's revision does not move and its protected content is byte-identical, so both author-written lines stay valid untouched and the generation publishes — which is exactly the branch row `C3` was already exercising | `test_c10_same_content_bounded_fix_needs_no_refresh_at_all` |

## D. Recovery, resume, staleness and concurrency

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| D1 | No-content disposition-only update | P | a republication with an empty intervening interval | `test_d1_disposition_only_update` |
| D2 | Stale bundle vs durable state | P | `assert_local_generation_matches` and the interval check both refuse `/approve-review` | `test_d2_stale_bundle_vs_durable_state` |
| D3 | Interrupted generation: state written and committed, bundle never generated | P | a fresh session regenerates for the same head with no second durability commit and no revision change | `test_d3_interrupted_generation_resumes` |
| D4 | Concurrent unrelated commit on an excluded path | P | both approvals stay `CURRENT`; `review_content_id` is not a function of live HEAD | `test_d4_concurrent_excluded_commit` |
| D5 | Cold resume from the on-disk repository alone | P | `implementing_entry_reachable`, `select_next_checkpoint` rule 1, the provenance interval and the generation metadata all re-derive | `test_d5_cold_resume` |
| D6 | Interrupted between claim publication and the state write | P | `CONTINUE_CLAIM` with the same owner token; the round completes from there | `test_d6_interrupted_between_claim_and_state_write_continues` |
| D7 | Interrupted between the completion commit and the release | P | the design's single automatic release, then `FRESH` on the next checkpoint | `test_d7_interrupted_between_commit_and_release_resumes` |
| D8 | Dirty resume with no worktree identity record | P | `WorktreeIdentityMissingError` with ownership evidence attached | `test_d8_dirty_resume_without_a_worktree_identity_refuses` |
| D9 | Foreign claim | P | `CheckpointOwnedByOtherWorktreeError`, evidence naming an executable escape literal | `test_d9_foreign_claim_refuses_without_an_explicit_takeover` |
| D10 | Interrupted `/approve-review plan` (journal open, no commit) | P | `NOT_COMMITTED` classified from durable Git state, guarded rollback, zero state bytes written, the pending files intact, and a fresh approval succeeding | `test_d10_interrupted_plan_approval_rolls_back_and_retries` |
| D11 | Second `/approve-review plan` while a journal is open | P | `PlanApprovalTransactionInProgressError`, evidence, a generic go-ahead refused, and the evidence-derived literal rotating the token | `test_d11_second_invocation_refuses_and_names_the_takeover_literal` |
| D12 | Protected plan edit after approval | P | recomputation stales `plan_approval`; `implementing_entry_reachable` and `/milestone-implement` step 1a refuse | `test_d12_plan_edit_after_approval_stales_the_plan_approval` |
| D13 | Missing bundle | P | `MissingRequiredBundleFileError` from `compute_bundle_id` and from the manifest writers | `test_d13_missing_bundle_refuses_by_name` |
| D14 | Two work items of different types running concurrently | P | `B7`: `D1`'s "resume-focus pointer, not an execution lock" end to end — neither item stales nor unclassifies the other, routing never steals focus, and completing one releases the pointer without touching the other | `test_d14_two_typed_work_items_do_not_interfere` |

## E. Invalid, missing and malformed state

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| E1 | Invalid `work_item_type` (unknown string) | P | documented refusals at both stages, no raw exception | `test_e1_invalid_work_item_type` |
| E2 | Corrupt `work_item_type` (list, dict) | P | `I2`: a documented error on every stage's reader and at the real script boundary, never a bare `TypeError:` line | `test_e2_non_string_work_item_type` |
| E3 | Missing `work_item_type` (`null`) | P | documented refusal at both stages | `test_e3_missing_work_item_type` |
| E4 | Malformed persisted `review_content_manifest` | P | the live-state consumer refuses (`StalePlanApprovalRegistryReadError`) rather than raising `AttributeError` | `test_e4_malformed_approval_record` |
| E5 | Malformed review feedback | P | missing binding fields, a foreign work item, and a stale bundle id all refuse by name | `test_e5_malformed_feedback` |
| E6 | `BLOCK` laundered to `REVISE` after the pin | P | the durable pin still refuses both the gate and the basis | `test_e6_block_pin_survives_laundering` |
| E7 | `base_commit` conflict; terminal id reuse | P | `WorkItemDeclarationFactConflictError`, `WorkItemTerminalReuseError` | `test_e7_base_commit_is_immutable_once_declared` |
| E8 | `plan_revision` mirror disagreement; disagreeing base commit | P | both halves of the plan-stage preflight refuse before writing content | `test_e8_plan_revision_mirror_disagreement_refuses_before_generating` |
| E9 | Broken supersession chain | P | no unique tip → refused as ambiguity; the terminal continuity rule refuses a tip naming nothing the walk found | `test_e9_broken_supersession_chain_refuses` |
| E10 | Two ordinary-role commits for one revision | P | genuine ambiguity, never silently picked | `test_e10_second_ordinary_role_commit_in_a_chain_refuses` |
| E11 | Record commit touching a second path | P | `MalformedBundleGenerationRecordCommitError` | `test_e11_record_commit_touching_a_second_path_refuses` |
| E12 | Merge on the walked line | P | `HeadPastBundleGenerationRecordError` / `NonFirstParentProvenanceIntervalError` | `test_e12_merge_in_the_interval_refuses` |
| E13 | Legacy adoption freshness | P | `I6`: the adopted item's own declarations decide, both arguments are required, and the wrong item's sets really would have answered "not stale" | `test_e13_legacy_adoption_uses_the_adopted_items_own_declarations` |

## F. The sanctioned generators, and a repository adopting the workflow

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| F1 | A product work item created strictly through the generators | P | `B4`: the declaration on disk is byte-identical to the generator's output, and the item reaches `MILESTONE_COMPLETE` | `test_f1_generated_declarations_are_usable` |
| F1b | The same for a process work item | P | the process deliverable tree is what the round protects | `test_f1b_process_generated_declarations_are_usable` |
| F1c | A plan that records a technical decision | P | `B5`: `docs/TECHNICAL_DECISIONS.md` no longer fails the plan bundle closed | `test_f1_generated_declarations_are_usable_for_a_plan_touching_decisions` |
| F2 | A repository without the workflow directory layout | P | `O3`: `/milestone-plan` step 3 succeeds and the plan bundle generates | `test_f2_bootstrap_without_directories` |
| F3 | A product milestone with a realistic documentation and build footprint | P | ledger `I8`: `docs/UX_FLOWS.md`, `docs/DOMAIN_GLOSSARY.md`, `docs/adr/`, the root build files, the version catalog and the detekt config all classify at **both** stages from generated declarations; shared territory (`.github/`, `docs/agent-context/`, `docs/improvements/`) and the other type's tree stay excluded; self-protection intact; a genuinely novel path still fails closed; and the item reaches `MILESTONE_COMPLETE` | `test_f3_product_milestone_with_a_realistic_documentation_and_build_footprint` |
| F4 | Declaration repair after a plan approval | P | ledger `I9`: an `implementation_stage`-only edit leaves the plan-stage digest untouched; a `plan_stage` edit moves it while the reviewed content manifest stays byte-identical; the stored `status` still reads `CURRENT` while `approval_is_current`/`implementing_entry_reachable` say otherwise; the sanctioned repair is a fresh revision through the real two-stage review; and `resolve_own_registry_completion_status`' content-vs-classification boundary (ledger `O12`) is asserted rather than assumed | `test_f4_declaration_repair_after_a_plan_approval_stales_and_must_be_re_approved` |

## G. Replay of the live wedge

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| G1 | Structural replay of `workflow-v2-3-1` at `79afd07` | P | ordinary round 2, a REVISE remediated entirely in excluded content, a `same_content` republication pinning revision 2 with `Workflow-Supersedes` naming round 2's own ordinary record, and the round becoming approvable | `test_g1_live_wedge_replay` |

A second, non-hermetic replay was run once by hand against real history
and is recorded in `WORKFLOW_SYSTEM_AUDIT.md` §6: a disposable clone at
`79afd07` with the previous round's real `generation_head` restored,
where the shipped pre-repair script exits 1 with exactly the reported
reason and the repaired tooling publishes. It is deliberately not part of
this suite, which must stay independent of this repository's own history.

## I. First-attempt bundle-directory resolution

Added in convergence pass 10; `I6` added in pass 11. Every row here
invokes `scripts/prepare-ai-review.sh` exactly **once** per case and
requires that one invocation to succeed, so a regression cannot hide
behind a second attempt — which is precisely how ledger `I16` survived:
the failed first generation created the scoped directory the identical
second attempt then resolved. `I1`-`I5` cover the scoped side of the
resolver contract; `I6` covers the flat side, which `I5` asserted the
*answer* for without ever generating through it.

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| I1 | First plan bundle, brand-new `process` item | P | `resolve_bundle_dir(..., stage="plan")` is scoped before anything exists on disk, and the single generation succeeds; nothing is ever authored at `.ai-review/current/` | `test_first_plan_bundle_process_item_succeeds_first_attempt` |
| I2 | First plan bundle, brand-new `product` item | P | the same, for the type whose plan-stage half was type-blind for its whole history | `test_first_plan_bundle_product_item_succeeds_first_attempt` |
| I3 | Withdraw a published plan bundle, then regenerate | P | `withdraw_bundle`'s quarantine of `current/` does not send the next round back to the flat path (`I16`, `I18`) | `test_regeneration_after_withdrawal_succeeds_first_attempt` |
| I4 | First `/milestone-plan <child-id>` on a remediation child | P | a child resolves its **own** `.ai-review/<child-id>/`, with the parent's scoped directory sitting right beside it resolving nothing for it, and the parent's published bundle surviving untouched | `test_first_plan_bundle_for_a_remediation_child_succeeds_first_attempt` |
| I5 | Every non-plan stage, before and after a scoped layout exists, and across a withdrawal | P | the flat compatibility layout is preserved exactly where it is a reachable generation target, and abandoned nowhere else | `test_non_plan_stages_keep_the_flat_compatibility_rule` |
| I6 | A **successful generation** through the omitted-`[work-item-id]` form, at every stage whose id argument is optional | P | that the compatibility rule is a usable generation target rather than only a resolver answer: `<bundle_dir>` is resolved through the production resolver, the inputs are authored there, `prepare-ai-review.sh` is run with the id omitted, and that one invocation publishes at `.ai-review/current/` without ever creating the scoped layout | `test_a_flat_compatibility_generation_succeeds_first_attempt` |

| I7 | The **given-id** split on a fresh tree, at the implementation stage | P | ledger `O32`: after the gitignored, documented-disposable `.ai-review/` tree is deleted between rounds, passing the optional `[work-item-id]` while the scoped root is absent refuses (`assert_review_request_states_review_content_id`, naming the scoped `REVIEW_REQUEST.md`) rather than withdrawing — nothing published, no marker, the flat bundle intact — and the identical second attempt, the same command text re-followed, succeeds | `test_the_given_id_split_on_a_fresh_tree_is_fail_closed_and_self_healing` |

## J. Cross-stage bundle-directory reuse and the manifest-producer gate

Added in convergence pass 12 (ledger `I20`). `$BUNDLE_DIR` is reused
across stages by design, and two of the four generation stages write no
`MANIFEST.md` at all. Every row drives the real generator as a
subprocess, at every published-stage / consuming-stage pairing the
directory can actually reach.

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| J1 | `functional-review` over an **implementation-published, technically-approved** scoped bundle | P+P | ledger `I20`: not withdrawn, not quarantined, archive present, `WORKFLOW_STATE.json` and `technical_approval` byte-identical, functional-review outputs generated into both the directory and the archive, previous `MANIFEST.md` untouched — and the round is still *usable*, continuing to `MILESTONE_COMPLETE` | `test_j1_functional_review_over_a_published_implementation_bundle` |
| J2 | The same over a **plan-published** bundle | P+P | the defect was stage-blind: it lived in the generator's own control flow | `test_j2_functional_review_over_a_published_plan_bundle` |
| J3 | The same over a **post-fix-published** bundle | P+P | including at revision 2, so the round identity is a real one rather than a first round | `test_j3_functional_review_over_a_published_post_fix_bundle` |
| J4 | A genuine `post-fix` round **after** a `functional-review` run | P+P | no stale filesystem artifact decides the next invocation either: the real round writes its own manifest, finalizes it, and manifest / on-disk / archived `bundle_id` agree again | `test_j4_a_genuine_generation_after_a_functional_review_run_still_finalizes` |
| J5 | A stale `IMPLEMENTATION_SUMMARY.md` counter at a stage that **does** write a manifest | P+P | the control the repair must not break: still withdrawn, quarantined, archive gone | `test_j5_a_stale_marker_line_at_a_real_generation_stage_still_withdraws` |
| J6 | The same stale line at the stage that writes **no** manifest | P+P | its mirror: a run with no identity of its own withdraws nothing | `test_j6_functional_review_never_withdraws_even_with_a_stale_marker_line` |
| J7 | The inherited manifest is reported | P+P | ledger `O30`: the run says on `stderr` that the manifest it inherited no longer describes the directory or the archive | `test_j7_a_non_manifest_stage_reports_the_inherited_manifest` |
| J8 | Approval over an inherited manifest | P+P | ledger `O30`'s non-gating evidence: every consumer *recomputes* `bundle_id`, so feedback naming the pre-regeneration identity no longer matches and `assert_feedback_matches_bundle` refuses — fail-closed, not silently accepted | `test_j8_approval_is_fail_closed_over_an_inherited_manifest` |

## K. The two approval bases

Added in convergence pass 12 (ledger `O35`). `USER_OVERRIDE` appeared
nowhere in this suite before it, because the helper called
`assert_feedback_matches_bundle` — a step `/approve-review` step 2
explicitly does not instruct — one step ahead of the decision.

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| K1 | Feedback saying `APPROVE` but naming a stale bundle | P+P | the binding mismatch is observed and reported, not fatal; the basis degrades to `USER_OVERRIDE`; the approval completes and the round accepts | `test_stale_feedback_resolves_user_override_and_still_approves` |
| K2 | A `REVISE` verdict | P+P | the other documented route to the same basis | `test_a_revise_verdict_also_resolves_user_override` |
| K3 | Matching feedback | P+P | the control: relaxing the helper's own extra assertion did not relax the decision — `EXTERNAL_APPROVE`, no mismatch reported | `test_matching_feedback_still_resolves_external_approve` |
| K4 | `BLOCK`, then D2a's laundering path | P+P | neither basis is reachable, at **both** enforcement points and in the command's real ordering: `technical_approval_gate_reachable` refuses the literal `BLOCK` and refuses again on `pinned_block` alone after the file is rewritten to `REVISE`, and `resolve_approval_basis`'s independent second layer is asserted directly because the gate means it is never reached | `test_a_block_verdict_reaches_neither_basis` |

## L. The plan document's generated checkpoint table

Added in convergence pass 12 (ledger `I22`).

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| L1 | `/apply-plan-review` step 5 as it read **before** the repair | P+P | the defect executed: a revision adding `CP2` leaves the document declaring only `CP1` while the registry declares both, and the plan bundle publishes cleanly — the table is not hashed separately and `assert_stage_completeness` checks only the `(Revision N)` marker | `test_the_pre_repair_sequence_publishes_a_table_the_registry_contradicts` |
| L2 | The same revision with step 5's re-embed performed | P+P | the repair: document and registry agree, and the bundle still publishes | `test_the_repaired_sequence_keeps_the_table_and_the_registry_in_agreement` |
| L3 | A revision that changes no checkpoint | P+P | why the command says to re-embed *unconditionally*: the render is a pure function of the checkpoint set, so it is a byte-identical no-op — a conditional step would only reinvite the judgement call that produced the staleness | `test_the_re_embed_is_a_no_op_when_no_checkpoint_changed` |

## M. The technical-approval commit's own contract

Added in convergence pass 12 (ledger `I21`).
`validate_technical_approval_commit` had **zero** production call sites.

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| M1 | An approval commit that also adds a second work item's entry | P+P | `discover_technical_approval_commit` accepts it — which is the defect — and the now-named check refuses it by work-item id | `test_a_commit_touching_another_work_item_is_refused` |
| M2 | An approval commit recording `technical_approval` without transitioning `phase` | P+P | the second half of the validator's contract | `test_a_commit_recording_the_approval_without_the_phase_is_refused` |
| M3 | An approval commit changing a top-level routing field | P+P | item 267's forbidden mutation, at this commit class | `test_a_top_level_routing_field_change_is_refused` |
| M4 | The ordinary approval commit every other row produces | P+P | the control: the repair constrains malformed commits without refusing real ones — both post-commit checks pass | `test_the_ordinary_approval_commit_passes_both_post_commit_checks` |

## N. The canonical `review_content_id` recipe

Added in convergence pass 12 (ledger `O31`). The recipes
`docs/ai-workflow/REVIEW_PROTOCOL.md` now documents are **executed**
here, not asserted as text.

| # | Scenario | Types | Proves | Test |
|---|---|---|---|---|
| N1 | The documented plan-stage recipe | P+P | it reproduces the value the generator's own `--write-manifest` step independently recorded | `test_the_documented_plan_stage_recipe_reproduces_the_manifest` |
| N2 | The documented implementation-stage recipe | P+P | the same, through `workflow_state.approval_review_content_id` | `test_the_documented_implementation_stage_recipe_reproduces_the_manifest` |
| N3 | The worktree-source variant the protocol warns against | P+P | the caution is load-bearing: it diverges the moment the tree is dirty, which is the ordinary state mid-round, while the documented recipe still answers the manifest's value | `test_the_recipe_the_protocol_warns_against_does_not_reproduce_it` |
| N4 | The wrong `artifacts_path` the protocol warns against | P+P | it computes a different work item's classification, and the digest moves with it | `test_the_default_artifacts_path_the_protocol_warns_against_differs` |
| N5 | A `bundle_id: <64 hex>` line in `REVIEW_REQUEST.md` | P+P | ledger `O37`, the fourth author-written generation precondition and the only prohibition among them: the generation **refuses** (`ForeignBundleIdFieldError`, naming the file), `current/` is intact, nothing is published and nothing is withdrawn — and removing the line is the whole remedy, with the corrected generation publishing first attempt | `test_a_foreign_bundle_id_line_refuses_the_generation` |

---

## Execution record

`python3 scripts/workflow_acceptance_matrix_test.py` — **146 tests, OK
(18 skipped)**. 128 tests actually execute, covering 94 distinct
scenarios: 34 of them are **P+P** rows, which run twice, once per
work-item type (34 x 2 + 60 = 128). The 18 skips are type-independent
rows the product subclass
declines by name. Wall clock ≈ 70 s. Also run in CI.

Convergence pass 12 added 49 tests: section `J` (16), section `K` (8),
section `L` (6), section `M` (8), section `N` (10), and one row in
section `I` (1).

Pre-repair behaviour, as evidence that the matrix actually binds. Each
control arm is built from the repaired sources with exactly one repair's
hunks reverted, so the failure set attributable to each repair is
isolated rather than inferred. Every arm below was **re-measured against
this pass's suite**; the figures pass 10 recorded were taken against
smaller suites and, worse, counted the skipped rows as green passes, so
their columns did not sum to the suite they described (convergence pass
11, ledger `O26`).

Counting rule, stated because the old table did not have one: a **test**
is one `unittest` test method — a row, or one type's half of a **P+P**
row. `fail + pass + skip = 146` in every arm. Sub-test expansions inside a
row are not counted separately (they inflate `unittest`'s own
`failures=`/`errors=` summary line, which is why arm 3 below reports its
`F3` failure as `errors=9` while exactly one row fails).

Each arm's mutation, so it is reproducible rather than described:

1. `git show 09afc38 -- scripts/prepare-ai-review.sh` reverse-applied
   (`R1`, campaign 1: the round-identity preflight);
2. `enter_self_reviewing_implementation`'s body replaced by
   `return state` (pre-repair behaviour: the wrap-up branch named no
   writer);
3. `git show 3c61b4b -- scripts/workflow_state.py` reverse-applied (the
   ledger-`I8` declaration hunks);
4. `resolve_bundle_dir`'s `if stage in SCOPED_BY_CONSTRUCTION_STAGES:
   return scoped` branch deleted (pre-repair behaviour for ledger `I16`);
5. `resolve_bundle_dir`'s existence gate keyed on `scoped` rather than
   `scoped_root` again (pre-repair behaviour for ledger `I18`);
6. arms 4 and 5 together, i.e. the pre-repair resolver verbatim;
7. `prepare-ai-review.sh`'s `if (( MANIFEST_WRITTEN )); then` restored to
   `if [[ -f "$BUNDLE_DIR/MANIFEST.md" ]]; then` (pre-repair behaviour
   for ledger `I20` — the stale-file-existence finalization decision).

| # | Control arm | Fail | Pass | Skip |
|---|---|---|---|---|
| 1 | `R1` hunk of `prepare-ai-review.sh` reverted | 16 — the `same_content` and provenance-recovery families (`B3`, `B4`, `B5` for both types; `B7`, `B11`, `B12`, `C3`, `D1`, `G1`, and pass 11's `C10`) plus the three malformed-`work_item_type` rows (`E1`-`E3`) | 112 | 18 |
| 2 | `enter_self_reviewing_implementation` → `return state` | 2 — `B14`, `C5` | 126 | 18 |
| 3 | ledger-`I8` declaration hunks reverted | 1 — `F3`, on exactly the eight unclassified paths plus the bundle-generation refusal | 127 | 18 |
| 4 | `resolve_bundle_dir`'s plan-stage branch removed | 122 | 6 | 18 |
| 5 | `resolve_bundle_dir` keyed on `current/` again | 3 — `B6`, `I5`, and pass 11's `C9` | 125 | 18 |
| 6 | arms 4 + 5, the pre-repair resolver verbatim | 122 | 6 | 18 |
| 7 | `I20`'s producer-state gate reverted to the file-existence test | 14 — `J1`, `J2`, `J3`, `J4`, `J6`, `J7`, `J8` for both types | 114 | 18 |

Arm 5's figure is the one pass 10 recorded as "5 fail / 87 green". Five
was `unittest`'s `failures=` count, which expands `I5`'s four sub-tests
into four entries; three *rows* fail, and 87 double-counted the 16 skips
of the day as passes.

Arm 7 is convergence pass 12's own. The two survivors are exactly `J5`
(both types) — the row asserting that a *genuine* stale marker line at a
manifest-writing stage still withdraws, which is correct in both worlds
and is the control the repair must not break. Arms 4 and 6 grew from 75
to 122 because pass 12's new sections all reach plan-stage resolution;
the mutation is unchanged.

Every figure in both tables was re-measured against this pass's suite by
running each arm's mutated tooling in an isolated copy of `scripts/`,
never by editing the working tree.

And the arm that *is* ledger `I17`, rather than evidence for another row:

| Control arm | Fail | Pass | Skip |
|---|---|---|---|
| repaired resolver, `Item.bundle_dir` restored to its hardcoded path | 0 | 79 | 18 |

(That arm's figures are pass 10's own and are not re-measured here: it is
evidence *about* ledger `I17`, not evidence for a repair the current
suite binds, and re-running it against a suite whose helper no longer has
a hardcoded path would measure a different thing.)
| pre-repair resolver (arm 6), `Item.bundle_dir` restored to its hardcoded path | 6 | 73 | 18 |

With the substitute in place the matrix is green whatever the resolver
does. The 6-versus-75 contrast measures what it was hiding: 69 further
tests became sensitive to `resolve_bundle_dir` only once
`Item.bundle_dir` actually called it.

Those surviving 6 are **not** "only the rows pass 10 added", as pass 10's
own text said. Five are section-`I` rows (`I1`-`I5`), which call the
resolver directly and are indeed pass 10's. The sixth is `B6`, a
**pre-existing** implementation-lifecycle row that pass 10 made
resolver-sensitive by repairing `Item.bundle_dir` — it fails here because
the post-withdrawal regeneration it asserts is exactly ledger `I18`'s
defect, not because it was added alongside them (ledger `O26`).

## Row withdrawn in convergence pass 7, and its subject retired in pass 8

The pre-repair `C5` also carried an end-to-end
`/accept-scoped-remediation` scenario. It was **withdrawn** in pass 7, not
silently dropped: its precondition (`AWAITING_FUNCTIONAL_REVIEW` with the
item's own registry non-terminal) is unreachable through any `"2.1"`
command chain — ledger `I10` carries the closure argument. The old row
manufactured that precondition by hand-writing `phase`, which is `O9`.

Pass 7 left the machinery covered by unit tests
(`workflow_state_test.py`'s `TestScopedRemediationEndToEnd`, 11 tests —
pass 7's own text said 13, which was wrong and is corrected here) on the
recorded reasoning that a future round would decide between giving the
entry a real writer and retiring the command. **Pass 8 took the second
option**: `/accept-scoped-remediation`, its gate function, its evidence
guard, its confirmation parser, its replay classifier and its acceptance
writer are all deleted, and so are the 43 unit tests that only proved that
dead path (two gate-truth-table tests also collapse into one, and one new
test pins the replacement operator guidance: `workflow_state_test.py` goes
653 → 610). `I10` is now `FIXED`, and the matrix has no
withdrawn row left to explain — there is no longer a command for the
withdrawn row to have exercised.

What the matrix still proves at this gate, unchanged: `C5`'s honest tail
(outstanding checkpoint → real transition → bundle → approval →
functional gate → `/accept-milestone`), `C8`'s bounded functional-fix
round with its own round-scoped checklist evidence (`I7`), and `C6`'s
remediation-child flow.
