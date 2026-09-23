# Active Milestone

## Milestone

`workflow-controller-automatic-lifecycle-orchestration` -- Controller automatic lifecycle
orchestration (governing workflow version `"2.2"`).

## Goal

Controller Generation 1 reports every implementation-stage phase and launches workers only for
the plan stage. This milestone makes it drive the automation-safe implementation-stage actions.
One `workflow-controller run` then carries a `"2.2"` work item from `IMPLEMENTING` through
checkpoint implementation, the final self-review pass, local implementation review and local
`REVISE` remediation, up to the next genuine human gate, without weakening any Workflow
guarantee. It adds one general automatic-dispatch rule, implementation-stage expected outcomes
with artifact postconditions, protocol-2.2 correctness fixes, worker lifecycle and concurrency
safety, and role-based model/effort routing.

The approved plan is the scope authority:
`docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md` (revision 5, approved at
`035c320`), with its registry and requirements mapping under `docs/ai-workflow/registry/` and
`docs/ai-workflow/requirements/`. Its "Non-goals" section lists what stays out of scope. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-worker-execution-hardening.md`.

## Checkpoint progress

Ground truth is `docs/ai-workflow/WORKFLOW_STATE.json`.

- **CP1 -- complete.** Plan-stage decision golden, and the implementation-stage evidence
  readers. None of the readers is wired into a decision yet.
  - **First act, before any other change:** `tests/golden/generate_plan_stage_decisions.py`
    generated `tests/golden/plan_stage_decisions.json` from the base commit's unchanged
    `decision.py`/`evidence.py`. It holds 37 evidence scenarios (from the existing plan-stage
    tests: verdicts, admissibility clauses, stale-bundle recovery variants, the `REJECTED`
    marker) at all 16 reachable plan-stage `(phase, version)` combinations: 592 cases, 62
    distinct decisions. Each decision body is stored once under a content-derived id, which
    keeps the file at about 150 KB instead of about 480 KB.
    `tests/test_golden_plan_stage_decisions.py` re-derives every case, normalises it with the
    generator's own `normalise` (temp root -> `<ROOT>`, 40-hex tokens -> `<SHA>`), and requires
    the result to be byte-equal to the golden.
  - `WorkItemView` gains `implementation_review_stages` (verbatim) and
    `technical_review_block_pins` (a verbatim tuple, `()` when absent). A present pins value
    that is not a list of objects is `MalformedWorkflowStateError`, so it is never read as "no
    pins".
  - `evidence`:
    - `_MANIFEST_LABELS` gains `review_content_id`, `implementation_revision`,
      `reviewed_implementation_head` and `worktree_root`.
    - `implementation_bundle_coherence(...) -> (coherent, clause, detail)`, with the clause
      codes `IMPLEMENTATION_BUNDLE_CLAUSES`.
    - `read_review_request_fields`, which uses the generator's own whole-file
      `review_content_id:` pattern.
    - `read_implementation_review_ledger -> LedgerView`. It is fail closed: a malformed ledger
      reads as no stage recorded, and `malformed` says why.
    - `committed_work_item` and `committed_checkpoint_statuses`.
    - `commit_trailers`, `bundle_generation_record_role` and
      `generation_record_view -> GenerationRecordView`.
    - `evaluate_manual_implementation_stage_admissibility` and
      `evaluate_apply_implementation_review_admissibility`.
  - `tests/fixtures.py`: `build_manifest_text` gains the implementation-stage labels as
    optional lines (output is byte-identical when they are omitted), and the new helpers are
    `target_worktree_root`, `build_implementation_manifest_text` and
    `write_implementation_manifest`.
  - Judgment calls, where the plan text left a detail open:
    - The manual evaluator takes an extra keyword, `current_worktree_root`. It is pure, and
      its `worktree_root` clause needs the live value.
    - `committed_checkpoint_statuses` is keyed by `(work_item_id, checkpoint_id)` tuples, as
      the plan's prose says.
    - `GenerationRecordView` also carries `head`, so CP4B's malformed-T gate can name the
      commit.
    - `newer_records` counts every commit that carries this work item's record trailer pair,
      whatever its role, the same way Workflow's own discovery does.
    - `build_manifest_text` gains four optional labels. The plan says three, but the
      implementation manifest helper needs all four.
  - Verified with `python3 -m unittest tests.test_golden_plan_stage_decisions
    tests.test_evidence tests.test_target_state`: 199 tests OK.
    `tests.test_write_containment tests.test_package_structure`: 17 tests OK.
    `python3 tests/golden/generate_plan_stage_decisions.py --check` reports the golden as
    current.
  - Mutation checks run in a scratch copy were all caught: changed plan-stage gate or reason
    text fails the golden, and removing the work-item check from the record role, the
    worktree-root clause, the hard `REVISE` bundle-id clause, the ledger's `APPROVE`-only rule
    or the committed read each fail the new tests.
- **CP2 -- complete.** Expected outcomes for the implementation stage, in `controller/job.py`
  only. No decision changes yet: `evidence.decide` still declines or gates every
  implementation-stage phase until CP3's dispatch rule, and the plan-stage golden is unchanged.
  - `ExpectedOutcome.postcondition`/`postcondition_phases` became
    `postconditions: tuple[(frozenset[phase], PostconditionFn), ...]`. The seven plan-stage rows
    moved to single-entry tuples with unchanged behaviour. `_row_clauses_failure` evaluates the
    one entry whose phase set holds the observed phase (`_postcondition_for_phase`), so
    `execute_step`, `_reconcile_completed` and `_reconcile_launched` still share one helper.
    `property_table_violations` requires each entry to be a non-empty phase set paired with a
    callable. The phase sets must be pairwise disjoint and each a subset of `to_any_of`. A row
    may declare none.
  - `PRE_STATE_FIELDS` gains `bundle_manifest_bundle_id`, the pre-state manifest's `bundle_id`
    (`None` for the bootstrap). The field count goes from 17 to 18. A record without the field
    reads as "not satisfied".
  - Plan-stage `BLOCK`-predicate fix. Rows 3/3' and row 16 share
    `_block_feedback_bound_to_pre_state_bundle`. It compares the feedback's
    `Reviewed bundle ID` with a non-null `bundle_manifest_bundle_id`, never `current_bundle_id`.
    The plan-stage role is normalised as before, and the implementation role must match
    exactly. Rows 3/3' `predicate_inputs` are now `{bundle_manifest_bundle_id}`.
  - Seven rows, 12-18. `EXPECTED_OUTCOMES` goes from 11 to 18 rows, as in the plan table, with
    no `"1"` row:
    - `_predicate_checkpoint_completed_durably`;
    - `_postcondition_self_review_entered_durably`;
    - `_postcondition_implementation_bundle_coherent` (the `REJECTED` marker first, then
      `implementation_bundle_coherence` at the live `HEAD`);
    - `_postcondition_implementation_bundle_regenerated`;
    - `_predicate_local_implementation_block_current`;
    - the local and manual `APPROVE`/`REVISE` postconditions.

    `_INCOMPLETE_EFFECT_PHASES` stays empty.
  - `WriterCall.trailing_calls` is an explicit `(function, justification)` allowlist. Step 1f
    lists `committed_checkpoint_status` and `release_checkpoint`. Property 5 skips only those
    names, and only after the declared call. An entry that does not occur there is reported
    as stale. The table property also rejects an entry with no justification, a duplicate, a
    non-identifier, or the declared call itself.
  - `tests/fixtures.py` adds:
    - `write_registry`, `update_workflow_state`, `implementation_review_ledger`, `commit_paths`;
    - `build_implementation_target`, which builds a committed seed with `.ai-review/` ignored;
    - the scripted-worker helpers `forced_automatic_action`, `scripted_worker`,
      `complete_checkpoint_effect`, `generation_effect` and `review_writes_effect`, plus
      `state_entry`.
  - Judgment calls, where the plan text left a detail open:
    - The row tests drive the real `execute_step` with `evidence.decide` patched to the
      row's automatic action, because selection is CP3's. The worker is `fake_claude.py`,
      preceded by a scripted side effect. The resume tests capture the pre-state for real with
      `_capture_pre_state`, then apply the same effect.
    - Registry completion at `HEAD` reuses `target_state._resolve_registry_complete` over the
      committed statuses. That function is the one reader of the registry's checkpoint ids.
    - No `BranchSpec.within` was added. The first `BLOCK` bullet in `review-implementation.md`
      is A6's own, and a test pins that.
  - Changed test assertions. Each is an intended change:
    - the row count (11 -> 18);
    - the pre-state field count (17 -> 18);
    - the pre-CP1 table filter in `MissingGoverningVersionRowRegressionTest` now also excludes
      the new implementation-stage rows;
    - the postcondition-shape tests were rewritten for the per-phase form;
    - plan-stage `BLOCK` fixtures no longer seed `current_bundle_id="b" * 64`, which no real
      Workflow state has.
  - Verified with `python3 -m unittest tests.test_job tests.test_job_validation tests.test_resume`:
    209 tests OK. The full suite (`python3 -m unittest discover -s tests -t .`) ran 655 tests:
    OK, 2 skipped. `python3 tests/golden/generate_plan_stage_decisions.py --check` reports the
    golden as current.
  - Mutation checks run in a scratch copy. Each of these fails at least one test:
    - dropping the predicate's committed clause, its `HEAD`-moved clause or its
      already-complete exclusion;
    - dropping either committed clause of the self-review postcondition;
    - dropping the regeneration `generation_head` clause, or the `REJECTED` clause;
    - reverting row 3 to `bundle_id`, or dropping its null guard;
    - accepting any role at row 16;
    - evaluating a postcondition at any phase;
    - dropping the stale-`trailing_calls` check or the overlap check;
    - dropping the local `APPROVE` content clause, the manual `APPROVE` feedback-binding
      clause, the manual `REVISE` unrecorded-stage clause, or the local `REVISE` work-item
      clause;
    - not capturing `bundle_manifest_bundle_id`.
- **CP3 -- complete.** The general automatic-dispatch rule, in `controller/decision.py` and
  `controller/evidence.py`.
  - `decision.AUTOMATIC_TRIPLES` is a literal copy of the 18 `EXPECTED_OUTCOMES` keys, held
    equal in both directions by a test. `classify_selected_action(phase, version, command)`
    compares `command_token(command)` (the slash-prefixed first word) and returns
    `ActionClassification(automatic, decline_reason)`. `apply_dispatch_rule` turns a handler's
    selection into the final decision. It runs at the end of `decision.decide`, of
    `evidence.decide`'s evidence-handler branch, and of `decide_no_work_item`, whose
    `(NO_PHASE, None, "/milestone-plan")` triple keeps it automatic. A declined selection keeps
    its phase, action and evidence, and carries the uniform reason: "`<phase>` selects
    `<command>`, which is model-invocable, but no verifiable ExpectedOutcome is declared for
    (`<phase>`, `"<version>"`, `<token>`), so this Controller reports it instead of launching
    it".
  - `REPORT_ONLY_PHASES`, `DECLINED_PHASES`, `GATE_REPORT_PHASES` and
    `_DECLINED_COMMAND_BY_PHASE` are deleted. Each phase now has its own handler in
    `_DISPATCH`: the four static gates share `_decide_static_gate` over `_STATIC_GATES`, with
    unchanged text, and `AMENDING_PLAN`/`AWAITING_LOCAL_IMPLEMENTATION_REVIEW` select their
    command.
  - `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` select `/milestone-implement <id>`. At
    `"2.1"`/`"2.2"` they gate first unless `plan_approval` is an object with `status ==
    "CURRENT"`. The gate's `what_is_required` is the plan's text, and its `safe_resume_command`
    is `workflow-controller explain --work-item <id>`.
  - The interim `_PHASES_AWAITING_EVIDENCE_HANDLER` holds `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`,
    `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` and `APPLYING_REVIEW_FEEDBACK`. Any action
    selected there is declined with its own interim reason, because the uniform text ("no
    ExpectedOutcome is declared") would be false there. CP4/CP4B remove it.
  - `SELECTED_COMMANDS` gains the four implementation-stage files (8 in total).
    `DELIBERATELY_NOT_SELECTED_COMMANDS` keeps the three functional-review commands,
    `bootstrap-workflow-v2` and `prepare-review` (5), with their reasons rewritten to the rule.
    The partition over the 17 installed files is 8/5/4.
  - Effects:
    - `/apply-functional-review` is declined where `execute_step` used to raise
      `AssertionError`.
    - `AMENDING_PLAN` reason text changes. It is the golden's one named exception.
    - The ten unreachable combinations are declined:
      - `REVISING_PLAN`, `AWAITING_LOCAL_PLAN_REVIEW` and
        `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` at `"1"`;
      - `AWAITING_EXTERNAL_PLAN_REVIEW` at `"2.1"`/`"2.2"`;
      - the five automatic plan-stage phases at version `None`.

      At base each raised `AssertionError` and left a `PLANNED` record, which was confirmed by
      running base code in a scratch copy.
  - Judgment calls, where the plan text left a detail open:
    - The plan-approval gate applies only at `"2.1"`/`"2.2"`. Those are the branches whose
      step 1a runs. `"1"` selects and is declined, as the plan's `"1"` `IMPLEMENTING` test
      requires even with `plan_approval` absent.
    - `apply_dispatch_rule` builds the declined `Decision` field by field, because the
      package-wide write-containment scan flags any `.replace(` call.
    - The golden test's exception, `_revert_permitted_difference`, accepts a case only if all
      of these hold:
      - the case is at `AMENDING_PLAN`;
      - it is declined on both sides;
      - it is equal apart from `reason`;
      - the golden has the base wording and the re-derivation has the uniform reason.

      A separate test requires the exception to cover exactly every `AMENDING_PLAN` case.
      `tests/golden/plan_stage_decisions.json` and its generator are unchanged. The
      generator's raw `--check` therefore now reports "differs": it has no exception, and the
      test is the authority.
  - Tests whose assertions change. Each is rewritten to the rule, and none is deleted without a
    replacement:
    - `test_decision`:
      - `ScopeAssertionTest` and `TwoShapeAssertionTest` are re-keyed by `(phase, version)`
        over `"1"`/`"2.1"`/`"2.2"`/`None`, with independent expectation tables;
      - `ProtocolTwoTwoCompatibilityParityTest` now asserts that classification equals triple
        membership, and that a triple never turns a gate into a launch;
      - `CommandFilePartitionTest` uses 8/5/4;
      - `Revision64PhaseWideningTest`'s report-only half is replaced.
    - `test_job`/`test_cli`: `"2.1"` `IMPLEMENTING` without an approval is now the gate, and
      a second test re-pins `DECLINED` at `"1"`.
    - `test_evidence`: unconsumed functional findings are declined.
    - `Protocol22ImplementationReviewGatesTest` now runs `tests/fake_claude.py` with a
      never-existing `FAKE_CLAUDE_REQUIRE_FILE` (fail-if-invoked) and asserts that no worker
      ran.
  - New tests:
    - `AutomaticTriplesTest`: equality in both directions, stems are a subset of
      `SELECTED_COMMANDS`, no user-only token;
    - `DispatchRuleTest`: a `mock.patch`-removed triple declines the same decision, and the
      bootstrap too; the interim phases are declined despite their triples; the uniform
      reason text is pinned;
    - `ImplementingDispatchTest`;
    - `PerPhaseSetRemovalTest`: the sets are gone, and no `controller/` module references
      them;
    - `test_job.DispatchRuleDeclineTest`: the functional-findings regression, and the ten
      unreachable combinations through `evidence.decide` and `execute_step` (one `DECLINED`
      record, no `PLANNED` flush). A positive control shows that the same fixtures launch
      where a triple exists.
  - Verified with `python3 -m unittest tests.test_decision tests.test_evidence tests.test_job
    tests.test_cli tests.test_golden_plan_stage_decisions tests.test_integration_disposable_repo`:
    301 tests OK, 2 skipped. The full suite (`python3 -m unittest discover -s tests -t .`) ran
    674 tests: OK, 2 skipped.
  - Mutation checks run in a scratch copy. Each of these fails at least one test:
    - ignoring the interim set;
    - classifying everything automatic;
    - dropping the rule from `evidence.decide` or from `decision.decide`;
    - dropping the plan-approval gate, applying it at `"1"`, or accepting any non-`None`
      approval;
    - adding a triple (`AMENDING_PLAN`) or removing one;
    - defining `REPORT_ONLY_PHASES` in `job.py`;
    - changing the uniform reason wording;
    - letting `decide_no_work_item` bypass the rule.
- **CP4 -- complete.** The implementation-review decision handlers, in `controller/evidence.py`
  and `controller/decision.py`.
  - **First act, before any code change:** `tests/golden/generate_external_implementation_review_decisions.py`
    generated `tests/golden/external_implementation_review_decisions.json` from CP3's unchanged
    code. It pins `"1"`/`"2.1"` `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` over 19 scenarios (38
    cases, 13 distinct decisions): the existing test cases, plus every input the new `"2.2"`
    handler reads (ledger, pins, scoped layout, `BLOCK`, the `REJECTED` marker). It reuses the
    plan-stage generator's normalisation and document form.
    `ExternalImplementationReviewGoldenTest` requires a byte-equal re-derivation, with no
    permitted difference.
  - Order inside `evidence.decide`:
    1. the `REJECTED` marker. `BUNDLE_BEARING_PHASES` gains `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`
       and `APPLYING_REVIEW_FEEDBACK`;
    2. the plan-bundle gate, unchanged;
    3. new: at `"2.2"` only, the implementation-bundle gate at the four
       `IMPLEMENTATION_BUNDLE_CONSUMING_PHASES`. It calls `implementation_bundle_coherence`, and
       requires a current `generation_head` everywhere except `APPLYING_REVIEW_FEEDBACK`;
    4. the per-phase handler.
  - The implementation-bundle gate, first form. An incoherent bundle names the failing clause
    and its detail, with `safe_resume_command` `workflow-controller explain --work-item <id>`. A
    `generation_head`-only failure keeps today's provenance text and names
    `/recover-implementation-provenance`. That text now lives in `_provenance_only_text`, shared
    with the `"1"`/`"2.1"` handler. CP4B replaces both texts.
  - The `REJECTED` gate has four branches, in order:
    1. `"2.2"` at an implementation-bundle-consuming phase: the marker-clearing clause, then
       `_implementation_bundle_recovery_steps`. The steps are:
       - step 0, when the bundle or an author file is absent or zero bytes: write
         `CONTEXT_FILES.txt` from the newest quarantine. At `APPLYING_REVIEW_FEEDBACK` the
         source is "the bundle REVIEW_FEEDBACK.md reviewed";
       - `IMPLEMENTATION_SUMMARY.md`'s `implementation_revision:`;
       - `REVIEW_REQUEST.md`'s `review_content_id:` from `approval_review_content_id(...)`;
       - `TEST_RESULTS.md`;
       - the generator, with the preflight clause.
    2. The plan-bundle-consuming phases: unchanged.
    3. `"1"`/`"2.1"` `APPLYING_REVIEW_FEEDBACK`: the clause, then the facts. The reviewed bundle
       was withdrawn. Step 1 refuses until a successful generation clears the marker. Restore
       from the newest quarantine, and rerun only if the regenerated `bundle_id` equals the
       feedback's. No generator is named, and `safe_resume_command` is
       `workflow-controller explain`.
    4. Everywhere else: today's bare text, byte-identical.
  - The generator `<stage>` comes from `implementation_generator_stages`. It reads the
    committed phase at the parent of this work item's most recent generation-record commit,
    found from the manifest's `generation_head`: the newest record in `generation_head..HEAD`,
    else `generation_head` itself when it is a record. `SELF_REVIEWING_IMPLEMENTATION` means
    `implementation`, and any other phase means `post-fix`. If no record is found, both forms
    are named.
  - Handlers:
    - `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` (new). A local `BLOCK` on file is the A7
      user-resolution gate. The role is compared case-insensitively and no binding line is
      required, which fails closed. Otherwise `/review-implementation <id>` is selected, and
      it is automatic at `"2.2"`.
    - `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, in this order:
      - no manual-role verdict: the manual gate, naming the bundle path, `bundle_id` and ledger
        `review_content_id`;
      - an inadmissible verdict (`evaluate_manual_implementation_stage_admissibility`): a gate
        naming every failing clause, including the step-1 reason for a `REVISE` bound to
        another bundle;
      - an admissible `BLOCK`: the resolution gate;
      - an admissible `APPROVE`/`REVISE`: `/record-manual-implementation-review <id>` is
        selected and is automatic. Advisories go into `evidence` and `reason`.
    - `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` splits by version. `"1"`/`"2.1"`/`None` keep
      the legacy handler byte for byte. `"2.2"` checks, in order:
      1. a manual `REVISE`/`BLOCK` bound to the manifest's `review_content_id`: the "later
         manual verdict" gate, with `safe_resume_command` `/apply-implementation-review`;
      2. the ledger has both stages `APPROVE` for the manifest's content, the feedback is
         `REVISE`/`APPROVE`, and no pin names the manifest's `bundle_id`: the
         `/approve-review implementation` gate;
      3. otherwise, a gate listing every missing clause ("/approve-review implementation would
         refuse here: ..."), with `safe_resume_command` `workflow-controller explain`.
    - `APPLYING_REVIEW_FEEDBACK`, at every version: the corrected static gate in
      `decision._STATIC_GATES`. `/apply-implementation-review` is legal from this phase,
      because it skips its own entry transition, and `safe_resume_command` is
      `/apply-implementation-review <id>`.
  - `_PHASES_AWAITING_EVIDENCE_HANDLER` shrinks to `{APPLYING_REVIEW_FEEDBACK}`.
  - `tests/fixtures.py` adds `write_implementation_bundle`: a coherent manifest plus the four
    author files, each stating what the generator requires of it.
  - Judgment calls, where the plan text left a detail open:
    - The `"1"`/`"2.1"` golden lives under `tests/golden/`, beside the plan-stage one. Its
      test is in `tests/test_evidence.py`.
    - The inadmissible manual gate's `safe_resume_command` is
      `/record-manual-implementation-review <id>`. This re-pins the original "names it, never
      launches" shape and mirrors the plan stage. Its `what_is_required` says to correct and
      re-paste first.
    - The `"2.2"` external handler's clause-1 role match is case-insensitive. Unlike an
      arrival test, it can only turn a decision into the "a human decides" gate, so a
      mis-cased manual verdict never falls through to naming approval.
    - The local `BLOCK` gate's `safe_resume_command` is `/review-implementation <id>`, as the
      plan stage's is `/review-plan <id>`.
    - `decision.decide`, which no launcher calls, now selects an automatic
      `/review-implementation` at `"2.2"` `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`. That is the
      same evidence-free shape its plan-stage placeholders have. `evidence.decide`'s handler and
      gates run first.
    - `tests/test_job_validation.py` is in the plan's file list, but needed no change: its
      rows force the selection.
  - Tests whose assertions change. Each is an intended change, and none is deleted without a
    replacement:
    - `test_evidence`: `AwaitingManualExternalImplementationReviewTest` now has coherent
      flat-layout fixtures. The two "names it, never launches" tests became automatic-ingestion
      assertions, and the shape is re-pinned for an inadmissible verdict.
      `test_2_2_item_reports_identically_to_2_1...` is replaced by
      `AwaitingExternalImplementationReviewTwoStageTest` and the golden.
    - `test_decision`:
      - `_INTERIM_PHASES` is now `{APPLYING_REVIEW_FEEDBACK}`;
      - `("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2")` moves to the automatic spot checks;
      - `Revision64PhaseWideningTest`'s decline test became an `evidence.decide` test:
        automatic with a coherent bundle, the coherence gate without one;
      - its interim-set half now asserts that both phases left the set and have evidence
        handlers;
      - `DispatchRuleTest`'s three-interim test now covers the one remaining interim phase.
    - `test_integration_disposable_repo`: `Protocol22ImplementationReviewGatesTest` has
      coherent fixtures and per-state expectations:
      - state 1 is automatic: one no-op fake invocation, `FAILED`;
      - states 2 and 3 are gates;
      - state 4 (no ledger) is the inadmissible gate;
      - new state 4' launches `/record-manual-implementation-review`;
      - state 5 is the ledger-incomplete gate;
      - new state 5' names `/approve-review implementation`.

      Every non-launching state runs fail-if-invoked, and its diagnostic file proves the fake
      never started.
  - New tests:
    - `AwaitingLocalImplementationReviewTest`;
    - `ApplyingReviewFeedbackCorrectedGateTest`;
    - `ImplementationRejectedMarkerGateTest`: all four `"2.2"` phases, plus branch 3 at
      `"1"`/`"2.1"`;
    - `ImplementationGeneratorStageTest`, against real git repositories;
    - `HumanGateShapeTest.test_applying_review_feedback_names_apply_implementation_review_as_legal`.
  - Verified with `python3 -m unittest tests.test_evidence tests.test_decision
    tests.test_job_validation tests.test_integration_disposable_repo`: 385 tests OK, 2 skipped.
    The full suite (`python3 -m unittest discover -s tests -t .`) ran 723 tests: OK, 2 skipped.
    `python3 tests/golden/generate_external_implementation_review_decisions.py --check`
    reports the new golden as current. The plan-stage golden test is green.
  - Mutation checks were run in a scratch copy, against the unmutated copy's baseline of 0
    failures. Each of these 25 mutants fails at least one test:
    - dropping the implementation-bundle gate, running it at every version, or always
      requiring `generation_head`;
    - applying `REJECTED` branch 1 at every version, or giving branch 3 the bare generator;
    - skipping manual admissibility;
    - dropping the local `BLOCK` gate, or matching its role exactly;
    - dropping any of the approval clauses: pin, feedback status, feedback absence, manual
      stage, ledger content;
    - dropping clause 1, or matching its role exactly;
    - routing `"2.2"` to the legacy handler, or every version to the two-stage one;
    - always naming both generator stages, or dropping the `generation_head` fallback;
    - keeping either phase in the interim set;
    - restoring the old `APPLYING_REVIEW_FEEDBACK` text;
    - dropping either new bundle-bearing phase;
    - dropping the provenance text.
- **CP4B -- complete.** Implementation-bundle recovery and row-18 remediation, in
  `controller/evidence.py`, `controller/decision.py`, `controller/job.py` and
  `controller/cli.py`.
  - The implementation-bundle gate, final form (`_implementation_bundle_gate`). It reads
    `implementation_bundle_coherence`'s clause and `generation_record_view`, never job records,
    and is evaluated in order:
    1. malformed `T`: `HEAD` is this work item's ordinary-role record and its committed phase
       equals `HEAD^`'s. The gate names the commit and `OPUS-R101-001`, says no Workflow command
       repairs it (a human repairs the unpushed history so the pending write lands alone before
       `T`, then reruns step 7), and resumes with `workflow-controller explain`;
    2. provenance only: the only failing clause is `generation_head`, `generation_head..HEAD`
       holds no record for this work item, and the phase is in
       `PROVENANCE_RECOVERY_LEGAL_PHASES` (a literal copy of
       `bundle_generation_recovered_role_legal_committed_phases("2.2")`, held equal by a test).
       The evidence lists each intervening commit, oldest first, with its subject and paths
       (`commits_since`). The text and `safe_resume_command` name
       `/recover-implementation-provenance` only on the excluded-only condition, and otherwise
       say to revert the commits or carry the change through a review round;
    3. everything else: the ordered steps of `_implementation_bundle_recovery_steps`, including
       a `generation_head`-only failure with a newer record (a `same_content` round whose
       generator failed). `safe_resume_command` is the steps, never the bare generator.
  - `"2.2"` `APPLYING_REVIEW_FEEDBACK` gets the automatic path in front of CP4's corrected gate
    (`_decide_applying_review_feedback_two_stage`). In order:
    1. absent or inadmissible feedback (`evaluate_apply_implementation_review_admissibility`)
       gives the corrected gate with the reason appended;
    2. the relaunch bound (`relaunch_bound_applies`) gives a gate naming J's id and status, the
       step-4/step-1 consequence, the `review-bundle.tar.gz` archive beside `current/`, and the
       human's two options. It resumes with `workflow-controller explain`;
    3. an unreadable committed phase at `HEAD` gives the corrected gate;
    4. otherwise `/apply-implementation-review <id>` is selected. It carries the pinned
       `PENDING_REVIEW_STAGE_WRITE_ADDENDUM`, formatted with the id and the committed phase,
       exactly when `HEAD` does not record `APPLYING_REVIEW_FEEDBACK`.

    `"1"`/`"2.1"` keep the static corrected gate unchanged.
  - Job history: `evidence.decide(..., *, last_apply_job=None)` takes an
    `evidence.LaunchedJobView`. `job.last_launched_apply_job_view` builds it from the runtime's
    `jobs/*.json`: the most recent (`created_at`, then `job_id`) terminal record for the target
    and work item that reached `LAUNCHED` from `APPLYING_REVIEW_FEEDBACK` with the
    `/apply-implementation-review` token. It skips `WorkerNotStarted` records and never raises
    on a job file. `execute_step` and `cmd_explain` both pass it.
  - Task assembly: `decision.Action` gains `task_addendum: str | None = None`. `job.worker_task`
    launches `command` or `f"{command}\n\n{task_addendum}"`, and `selected_action.task_addendum`
    records it (`null` for a bare task). `explain` prints the addendum, and `explain --json`
    gains `task_addendum`.
  - `_PHASES_AWAITING_EVIDENCE_HANDLER` and its `interim_decline_reason` are deleted.
    `InterimSetRemovalTest` asserts that neither exists and that no `controller/` module names
    either.
  - Judgment calls, where the plan text left a detail open:
    - Generator stage: with no manifest anchor (no readable manifest, or no `generation_head`
      line) and `HEAD` itself this work item's record, `HEAD`'s parent phase decides. Without
      this, a first-round final pass whose generator failed would name both forms, not
      `implementation`. A present but non-ancestor `generation_head` still names both forms.
    - An unreadable committed phase at `HEAD` gates (fail closed) rather than attaching an
      addendum with an unknown phase.
    - The malformed-`T` check needs a readable committed phase at `HEAD`.
    - A record missing `pre_state.bundle_manifest_bundle_id` reads as `None`, which the bound
      counts as the same bundle, rather than being skipped. The evidence side also re-checks
      J's command and phase.
    - `commits_since` follows the plan's `git log --reverse --name-only` without
      `--first-parent`.
  - Tests whose assertions change. Each follows from CP4B replacing CP4's first-form texts, and
    none is weakened:
    - `test_evidence`: the local/manual/external incoherent-bundle tests now assert the ordered
      steps (`_assert_regeneration_steps`). The two `generation_head`-only tests assert the
      conditional provenance text (`_assert_provenance_only`); one is renamed
      `test_a_generation_head_only_failure_names_the_conditional_provenance_recovery`.
    - `test_decision`: `_INTERIM_PHASES` is gone. The coherence-gate test asserts the steps.
      The interim-phase test became `test_the_former_interim_phases_classify_by_the_rule_alone`.
    - `test_integration_disposable_repo`: `_run` expects the command plus any recorded addendum
      as the worker's task.
  - New tests:
    - `ImplementationBundleRecoveryGateTest`, per ordered case against real repositories:
      malformed `T`, excluded-only commits, the legal-phase copy, `same_content` -> `post-fix`,
      ordinary post-fix -> `post-fix`, final pass -> `implementation` (first round and after a
      re-approval), and both forms;
    - `ApplyingReviewFeedbackAutomaticPathTest` and `PendingReviewStageWriteAddendumTest`
      (byte-for-byte pin, no user-only name, passes `_assert_not_user_only`);
    - `test_job`: `LastLaunchedApplyJobViewTest`, `ApplyingReviewFeedbackExecuteTest` (the
      addendum in the fake worker's recorded argv; FAILED/INTERRUPTED/INCOMPLETE bounds under
      the fail-if-invoked fake; FINISHED, another phase, another bundle; null bundle;
      `WorkerNotStarted` alone and in front of a real attempt; a non-terminal record never J),
      `WorkerTaskTest`, and a bare-task check in `LaunchPathTest`;
    - `test_cli`: `ApplyingReviewFeedbackCliTest`: `explain` shows the addendum, `explain` and
      `step` record the same gate, and `explain` tolerates an unparseable file, an abandoned
      minimal record and another target's record;
    - `Protocol22ImplementationReviewGatesTest` gains state 6 (automatic, with the addendum in
      the recorded task) and state 7 (the relaunch-bound gate naming state 6's job).
  - Verified with `python3 -m unittest tests.test_evidence tests.test_decision tests.test_job
    tests.test_cli tests.test_integration_disposable_repo`: 383 tests OK, 2 skipped. The full
    suite (`python3 -m unittest discover -s tests -t .`) ran 767 tests: OK, 2 skipped.
    `python3 tests/golden/generate_external_implementation_review_decisions.py --check` reports
    that golden as current. The plan-stage golden test is green; its generator's raw `--check`
    still reports the CP3-documented `AMENDING_PLAN` difference.
  - Mutation checks were run in a scratch copy. Each of these 25 mutants fails at least one
    test:
    - dropping the malformed-`T` case, the no-newer-record or legal-phase condition of the
      provenance case, or the `HEAD` fallback, or widening that fallback over a non-ancestor
      anchor;
    - the bound ignoring the bundle, a null bundle, `FINISHED`, or J's phase/command; no bound
      at all;
    - skipping admissibility; the addendum always or never; no unreadable-phase gate; the
      automatic path at every version; drift in the addendum text;
    - the builder keeping `WorkerNotStarted`, accepting non-terminal or never-launched records,
      ignoring the target or recency;
    - launching the bare command, not recording the addendum, or `execute_step`/`explain` not
      passing the job history.
- **CP5-CP9 -- pending**, in registry order.
