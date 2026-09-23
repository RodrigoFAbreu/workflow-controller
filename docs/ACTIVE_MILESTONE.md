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
- **CP5 -- complete.** Worker lifecycle and concurrency safety, in the new `controller/lock.py`
  and in `controller/worker.py`, `controller/job.py`, `controller/cli.py` and
  `controller/errors.py`. The manual external plan review's Optional findings O1-O4 are folded
  in where they fit this checkpoint, as the user decided. O4's last item, the small wording and
  citation drifts, concerns the documentation, so it is left to CP8.
  - `lock` sits after `runtime` in `DEPENDENCY_ORDER` and `__all__`.
    - `acquire_lifecycle_lock(target_root)` takes `flock(LOCK_EX | LOCK_NB)` on an `O_RDONLY`
      descriptor of `git rev-parse --absolute-git-dir`. Only `BlockingIOError` is
      `LifecycleWorkerActiveError` (exit 45). Any other `OSError` from `os.open` or `flock` is
      `LifecycleLockError` (exit 20), a sibling class, naming the path, the operation and the
      errno.
    - `probe_lifecycle_lock` never acquires. It reads `/proc/locks` by the `fdinfo` `mnt_id` ->
      `mountinfo` `MAJ:MIN` device and the `fdinfo` `ino:`, and answers `free` only from the
      init pid namespace. Every unreadable or unparseable read is `unknown`.
    - `read_pid_namespace()` is the one patchable reader of `/proc/self/ns/pid`.
    - O4: an unresolvable git directory is its own class, `GitDirectoryUnresolvableError`
      (exit 20).
  - `worker`:
    - `launch(..., timeout=None, pass_fds=(), on_spawn=None)`. `on_spawn` runs after `Popen`,
      outside the `OSError` -> `WorkerLaunchError` conversion. O4: on *any* `BaseException`
      from it, `KeyboardInterrupt` included, the group is killed and reaped and the original
      exception propagates.
    - `WorkerProcess` (`pid`, `pgid`, `start_ticks`, `boot_id`, `pid_namespace`, `hostname`,
      `machine_id`), `capture_worker_process`, `read_process_context`.
    - `assess_worker_liveness` -> `LivenessAssessment`, and `classify_worker_liveness`, its
      verdict. Keyed on `boot_id` first; a host is hostname *and* machine id. The process test
      is zombie-aware. Its `/proc` form is used only where `/proc/self` names the Controller's
      pid, and its `killpg` form answers at most `possibly live`.
    - O1: a process whose process-level state is `Z`/`X` still counts as running when a thread
      under `/proc/<pid>/task/` is running. A read error other than a vanished entry leaves
      the `/proc` form with no answer (fail closed to `killpg`). A member-scan `not live` is
      confirmed by one rescan.
    - O4: in the no-boot-identity row, a `pid_namespace` read on one side only is
      `unverifiable`.
    - O3: the assessment records which case produced `active` (`leader_running` or
      `member_scan`, with the members found).
  - `job`:
    - `execute_step` takes the lock first and holds it for the whole call. It refuses with
      `PendingJobReconciliationError` before deciding, then runs the unchanged nine steps in
      `_execute_step_locked`.
    - The `PLANNED` flush records `lifecycle_lock: {path}`. The worker inherits the descriptor
      (`pass_fds`), and the `on_spawn` flush of `worker_process` is a second `LAUNCHED` write.
    - `UserOnlyCommandError`/`WorkerLaunchError` from `launch` persist `FAILED`
      (`WorkerNotStarted`) before re-raising.
    - After the flush, a `KeyboardInterrupt` prints one stderr line (pid, pgid, `resume`) and
      propagates unchanged.
    - `DEFAULT_WORKER_TIMEOUT = None`.
    - `pending_reconciliation_jobs` is the one read-only scan behind the refusal and `explain`.
      Each entry names its clearing command: `resume`; `resume --abandon JOB_ID`; for a newer
      generation, that generation's `resume`; or, O2, removal by hand for an entry that is not
      a regular file.
    - `resume` takes the lock, except on a root that no longer resolves, and applies the
      liveness hold (`resume_marked.outcome` `worker_active`/`worker_unverifiable`).
      `_reconcile_launched` persists `FAILED` (`UnreconcilableJobError`) before raising for a
      record carrying `lifecycle_lock`. The three messages name `--abandon`, except for a newer
      generation's record.
    - `abandon` implements `resume --abandon`: the `JOB_ID` constraint, marking in place,
      replacing an unparseable or unknown-schema file (its bytes kept under `jobs/abandoned/`),
      and the liveness refusals.
    - The exit-45 lock message names the recorded worker only for an `active` verdict. O3:
      kill guidance for a matched leader; for a member-scan-only `active`, the members, to be
      verified before ending the group. It also carries the "inherited the descriptor"
      sentence.
    - O2: every job-file reader `lstat`s first (the pending scan, `resume`, the relaunch-bound
      history reader, and the exit-45 record read), so a FIFO, directory or symlink is never
      opened.
  - `cli`:
    - `EXIT_WORKER_ACTIVE = 45`, caught in its own clause before the blanket
      `ControllerError` -> 20.
    - `resume --abandon JOB_ID [--acknowledge-unverifiable-worker]`; the flag without
      `--abandon` is a usage error (exit 2). `cmd_resume` exits 45 for a held record.
    - `explain` prints the pending job files and `lifecycle lock: ...` ahead of its decision.
      O4: this precedes both the work-item and the no-work-item branch. `explain --json` gains
      `pending_jobs` and `lifecycle_lock`.
    - `inspect` reports `lifecycle lock: ...`, and `inspect --json` gains a top-level
      `lifecycle_lock`.
  - `tests/test_write_containment.py` gains the one read-only `os.open` acceptance
    (`os.O_RDONLY`, alone or `|`-combined with only `O_DIRECTORY`/`O_CLOEXEC`), with negative
    instantiations.
  - `tests/fake_claude.py` gains three modes: `FAKE_CLAUDE_HANG_UNTIL_FILE` (checked before
    the writes), `FAKE_CLAUDE_INVOCATIONS_FILE` and `FAKE_CLAUDE_GIT_COMMIT`. Its diagnostics
    gain `pid`, `pgid` and `fds`.
  - `tests/process_fixtures.py` (new) holds the process helpers:
    - the test-owned sleeper;
    - the subreaper `ZombieGroup` (a zombie leader, or a zombie-only group);
    - the `unshare -Urpf --mount-proc` probe runner;
    - scratch git repositories under the gitignored `build/`.
  - Judgment calls, where the plan text left a detail open:
    - `--abandon`'s liveness refusals exit 45. An `active` worker is `LifecycleWorkerActiveError`.
      An `unverifiable` one without the flag is `LifecycleWorkerUnverifiableError`, a subclass,
      mirroring `resume`'s 45 for `worker_unverifiable`. Its other refusals are
      `JobAbandonRefusedError` (exit 20).
    - `worker_liveness` is a top-level field of an abandoned record: the assessment plus
      `acknowledged`.
    - A marked record also gets `transition_verified: false` and `reconciled_at`.
    - The minimal replacement record also carries `schema_version: 1`. An existing
      `jobs/abandoned/<name>` is never overwritten; the copy gets a suffix instead.
    - `cmd_resume` gives a held record's exit 45 precedence over exit 40.
    - O2 takes the reviewer's alternative. An entry that is not a regular file is never opened,
      and it is named for removal by hand. Moving it into `jobs/abandoned/` would need a new
      write form outside `runtime`'s checked API. Behavior change: a symlinked job file that
      `resume` used to follow is now refused. CP8 documents the narrowed guarantee.
    - The Ctrl-C line is printed only when the interrupt reaches `execute_step` from inside
      `worker.launch`, after the flush. After `launch` returns, the worker has already
      exited.
  - Tests whose assertions change. Each is intended, and none is deleted without a
    replacement:
    - the status-sequence prefix and the two exact sequences gain the second `LAUNCHED`;
    - the `COMPLETED` key set gains `lifecycle_lock` and `worker_process`;
    - the `PLANNED` absent-field list gains `worker_process`, and the test now also asserts
      `lifecycle_lock`;
    - both end-to-end interruption tests take the orphan case's shape. The kill fires once
      `worker_process` is on disk; `resume` refuses until the orphan is released and gone; a
      cleanup registered before the child starts kills every recorded group; and the final
      `killpg` asserts that no fake outlived the test. They no longer leak sleepers;
    - CP4B's `test_a_non_terminal_record_is_never_j`: `step` now refuses with
      `PendingJobReconciliationError` beside a pending `LAUNCHED` record (it used to decide
      around it). J is still asserted never to be that record;
    - `ApplyingReviewFeedbackCliTest`'s setup moved into a shared fixture base, with no
      assertion change.
  - New tests:
    - `tests/test_lock.py`: contention in-process and cross-process; inherited descriptors,
      including a background grandchild, with the exit-45 message naming the recorded member
      and the other-holder sentence; `LifecycleLockError` for `ENOLCK`, `EBADF` and `EACCES`;
      the real-filesystem probe on btrfs `build/`, where `st_dev` `0:52` differs from the
      mount device `0:29` and the matched entry carries the mount device; the probe never
      taking the lock; every patched-`/proc` row; and the real `unshare` child pid namespace
      (`held`, then `unknown`, and the acquire refused). The `unshare` test ran here; it was
      not skipped.
    - `tests/test_worker.py`: `on_spawn`, `pass_fds`, and O4's `BaseException` kill-and-reap;
      `timeout=None`; the process test with real processes and with patched `/proc`; real
      subreaper zombies; O1's thread, fail-closed and rescan cases; every boot-keyed row,
      including O4's one-sided namespace.
    - `tests/test_job.py`: in-process concurrency (exit 45 through `cli.main`, one worker); a
      `SIGSTOP`ped worker counting as active; `WorkerNotStarted` for both errors; every
      pending-refusal case, including non-regular entries; the base-version `PLANNED` record;
      the `on_spawn` flush failure; Ctrl-C; the unbounded default; prose over state and its
      AST pin; the second `LAUNCHED` write.
    - `tests/test_resume.py`: the orphan and unreconcilable-orphan cases; a pre-milestone
      record cleared by `--abandon`; `--abandon` for a vanished work item, an unknown status,
      an unparseable file from another target, an unknown schema, and a newer generation; the
      `JOB_ID` constraint with the runtime byte-identical afterwards; zombie groups; every
      boot-keyed `resume`/`--abandon` case; no kill advice across boots from `step`, `resume`
      and `--abandon`; a root that is no longer a git repository.
    - `tests/test_cli.py`: the exit-code table and clause-order AST pin; the lock errors
      through `cli.main` for `step`, `resume` and `--abandon`; `cmd_resume` 45 for both holds;
      `explain` while a job is pending (round 4's O3), in both branches; the lock report.
  - Verified with `python3 -m unittest tests.test_lock tests.test_worker tests.test_job
    tests.test_job_validation tests.test_resume tests.test_cli tests.test_package_structure
    tests.test_write_containment`: 432 tests OK. The full suite (`python3 -m unittest discover -s
    tests -t .`) ran 890 tests: OK, 2 skipped. It leaves no `tests/fake_claude.py` process
    behind (0 before, 0 after), and no scratch directory under `build/` or `/tmp`.
    `python3 tests/golden/generate_external_implementation_review_decisions.py --check` reports
    that golden as current, and the plan-stage golden test is green.
  - Mutation checks were run in a scratch copy. 43 of 44 mutants fail at least one test.
    - Against `tests.test_job tests.test_resume tests.test_cli`, 29 of 30 are caught:
      - no lock in `execute_step` or `resume`; no pending refusal;
      - the pending scan opening non-regular entries (the FIFO hangs until the timeout),
        skipping unparseable files, or ignoring the target;
      - no `WorkerNotStarted` record, no `on_spawn` flush, no inherited descriptor;
      - no liveness hold, no terminal disposition;
      - `--abandon` accepting `unverifiable` without the flag, `active` with it, a terminal
        record or a newer generation, dropping the `JOB_ID` syntax check, or writing no copy;
      - no Ctrl-C line; the 3600 s default restored;
      - an unenriched exit-45 message, or the recorded group always named;
      - `--abandon` in a newer generation's clearing text, or missing from `resume`'s;
      - the blanket clause before the exit-45 clause; no exit 45 for a held record; no pending
        report in `explain` text or JSON; the flag accepted without `--abandon`;
      - every `OSError` treated as contention.

      The one survivor removes the `JOB_ID` scan-membership check. It is an equivalent
      mutant: a regular file `jobs/<stem>.json` is always in pathlib's `*.json` scan, dotfiles
      included, and the lstat and resolved-parent checks already refuse everything else. The
      check stays as the plan words the constraint.
    - Against `tests.test_worker tests.test_lock tests.test_resume`, 14 of 14 are caught:
      - zombies counted as running;
      - a host identified by hostname alone;
      - `killpg` success read as live;
      - the probe answering `free` from any namespace, or counting waiters;
      - no O1 thread check, no confirmation rescan, or an unreadable stat skipped;
      - O4's one-sided namespace accepted;
      - an `on_spawn` failure leaving the worker running;
      - the `/proc` form used from another namespace;
      - `stat` parsed at the first `)`;
      - a reused leader pid ignored;
      - a same-boot namespace mismatch ignored.
- **CP6 -- complete.** Role-based worker routing, in the new `controller/routing.py` and in
  `controller/worker.py`, `controller/job.py`, `controller/cli.py` and `controller/errors.py`.
  - `routing` sits between `evidence` and `worker` in `DEPENDENCY_ORDER` and `__all__`. It
    imports only `errors` and `decision`.
    - `ROLE_ROUTES` is the plan's table. `milestone-implement`, `apply-plan-review`,
      `apply-implementation-review`, `review-plan`, `review-implementation` and
      `milestone-implement-self-review` default to `claude-opus-5-5`/`xhigh`. The last three are
      single-agent. `milestone-plan`, `record-manual-plan-review` and
      `record-manual-implementation-review` inherit. `ROLES` is the closed role set.
    - `role_for(phase, command, registry_complete)` reads durable state only.
      `/milestone-implement` is the self-review role from `SELF_REVIEWING_IMPLEMENTATION`, and
      from `IMPLEMENTING` when `registry_complete is True`. Every other stem maps to its own role
      (`ROLE_BY_COMMAND_STEM`, held equal to `decision.SELECTED_COMMANDS` by a test).
    - `resolve_route` applies the precedence per field: `role-cli`, `cli`, `config-role`,
      `config-default`, `default`, `inherit`. `single_agent` is always the built-in value, and
      no override surface names it.
    - `ResolvedRoute.to_record()` is the job record's `worker_route`: `{role, model, effort,
      single_agent, fresh_session: true, sources: {model, effort}}`. `disallowed_tools` is
      `SUBAGENT_TOOLS` for a single-agent route, else empty.
    - `RoutingOptions` holds the command-line overrides and the parsed config.
      `NO_OVERRIDES` is the built-in table alone.
    - `load_routing_config`/`parse_routing_config` refuse every malformed file with
      `RoutingConfigError` (exit 20): unreadable, not JSON, not an object, a `schema_version`
      other than the integer `1`, an unknown top-level key, role or field key, a duplicate key,
      and a value that is not a non-empty string or that begins with `-`.
  - `worker.launch(..., model=None, effort=None, disallowed_tools=None)` appends `--model M`,
    `--effort E`, then `--disallowedTools A,B` as one comma-joined element, last. It never
    passes a session-reuse flag.
  - `job.execute_step(..., routing=routing.NO_OVERRIDES)` resolves the route
    (`_worker_route`) after the launch guard and before the `PLANNED` flush. It records
    `worker_route` from that flush on, and passes the route's model, effort and disallowed tools
    to `worker.launch`. No-launch records carry no route.
  - `cli`:
    - new global options `--model`, `--effort`, `--role-model ROLE=MODEL` and
      `--role-effort ROLE=EFFORT` (each repeatable, once per role), and `--routing-config PATH`;
    - an unknown role, a missing `=`, a second assignment of one role, or an empty or
      `-`-prefixed value is an argparse usage error (exit 2), before anything is written;
    - `cmd_step`/`cmd_run` build `RoutingOptions` once, before inspecting the target
      (`_routing_options`), and `_run_one_step` passes it to `execute_step`.
  - `errors.RoutingConfigError` (exit 20 through the blanket clause).
  - Judgment calls, where the plan text left a detail open:
    - `RoutingConfigError` lives in `errors.py` with the rest of the refusal taxonomy, although
      CP6's file list does not name `errors.py`.
    - A model or effort value must be non-empty and must not begin with `-`, on the command line
      and in the config file. Their vocabulary stays the `claude` CLI's, but no routed value can be
      read as an option, so "no argv ever contains a session-reuse flag" holds for every route.
    - Assigning one role twice is a usage error rather than last-wins, because it is ambiguous.
    - The config's `default` and `roles` are optional. A `null` value is refused, so the config
      cannot force `inherit` for a role that has a built-in model.
    - `registry_complete is True` is taken literally. `None` at `IMPLEMENTING` routes the
      checkpoint role. It cannot occur for a `"2.1"`/`"2.2"` item, which always declares a
      registry.
    - Only `step` and `run` read the routing options. `explain` and `inspect` accept them and
      ignore them. `cli` reads them with `getattr` defaults, as `cmd_resume` reads `--abandon`,
      so a hand-built namespace routes by the built-in table.
    - `SUBAGENT_TOOLS` is `("Agent", "Workflow")`. That was checked statically against the
      installed CLI (2.1.281): its bundle defines the `Agent` tool, with `Task` as an alias, and
      a `Workflow` tool. The same bundle also supports forked skills (`context: "fork"`), so
      CP9's live probe must settle whether `Skill` joins the set.
    - An automatic command with no role raises `AssertionError`, like
      `_expected_outcome_for`. It is raised before the `PLANNED` flush, so it leaves no record.
  - Test whose assertion changes (intended): `LaunchPathTest.
    test_completed_record_contains_every_schema_field_cp6_owns` gains `worker_route`.
    `tests/test_package_structure.py`'s `DEPENDENCY_ORDER` gains `routing`.
  - New tests:
    - `tests/test_routing.py`: the role set and table; the five named roles and the self-review
      pass at Opus 5.5 `xhigh`; single-agent roles and their disallow list; the inherit roles;
      role derivation for every automatic triple and the final pass; each precedence level
      beating every level below it, per field and independently; `single_agent` not
      overridable; value and role-assignment checks; every malformed config.
    - `tests/test_worker.py` `RouteArgvTest`: the unchanged argv without a route; the flag
      order with the disallow list last; each flag alone; every role's route under three
      override sets, with no session-reuse flag; no session-reuse literal in `worker.py`.
    - `tests/test_job.py` `WorkerRouteTest`: the route on every write from `PLANNED` on; each of
      the nine role cases reaching the record and the fake worker's argv; `sources` naming the
      winning level; an unroutable action leaving no record and launching nothing.
    - `tests/test_cli.py` `RoutingOptionsParserTest` and `RoutingOptionsPassThroughTest`: the
      options parse; every malformed option exits 2 and writes nothing; `step` and `run` pass
      the options and the parsed config; an invalid config exits 20 with no job record and no
      worker; a valid config routes the real launch.
  - Verified with `python3 -m unittest tests.test_routing tests.test_worker tests.test_cli
    tests.test_job`: 223 tests OK. The full suite (`python3 -m unittest discover -s tests -t .`)
    ran 932 tests: OK, 2 skipped. No `tests/fake_claude.py` process was left behind (0 before,
    0 after). `python3 tests/golden/generate_external_implementation_review_decisions.py
    --check` reports that golden as current, and the plan-stage golden test is green.
  - Mutation checks were run in a scratch copy, against the four CP6 modules. All 33 mutants
    were caught:
    - precedence reordered (role-cli below cli, config default above config role), the config
      ignored, or the job ignoring the routing options;
    - no final-pass self-review from `IMPLEMENTING`, `None` read as registry-complete, or no
      self-review from `SELF_REVIEWING_IMPLEMENTATION`;
    - a review or self-review role made multi-agent, a named role inheriting, an inherit role
      routed, `Workflow` dropped from `SUBAGENT_TOOLS`, or `single_agent` made routable;
    - `-`-prefixed values, duplicate keys, a boolean `schema_version`, unknown roles or unknown
      top-level keys accepted; `fresh_session` false;
    - the disallow list placed first or space-split; `launch` or the job dropping the model,
      effort or disallow list; the job not recording the route, resolving it after the
      `PLANNED` flush, or ignoring `registry_complete`;
    - `cli` not passing the options, never loading the config, dropping `--role-model`,
      accepting a duplicate role, or not validating `--model`.
- **CP7 -- complete.** The end-to-end lifecycle regression suite, in `tests/fake_claude.py`,
  `tests/fixtures.py` and the new `tests/test_lifecycle_orchestration.py`. No `controller/` change.
  - `tests/fake_claude.py` gains `FAKE_CLAUDE_SCRIPT`: a JSON file mapping an exact task string (the
    `-p` argument, addendum included) to an ordered list of per-invocation action lists.
    - Each invocation appends `{"task", "invocation"}` to the counter file beside it
      (`<script>.invocations`), then performs that task's next action list in the target.
    - A task the script does not name, or an invocation past its list, exits 92 before any action.
    - The actions are `write {path, text}`, `commit {paths, message}` (stages exactly `paths`) and
      `delete {path}` (a file or a directory tree). A failing action raises, so a script error is
      never silent.
    - `perform_actions` is the one implementation, shared by the worker process and in-process
      seeding.
  - `tests/fixtures.py` adds `script_write`, `script_commit`, `script_delete`, `trailer_message`,
    `write_worker_script`, `scripted_worker_tasks` and `perform_script_actions`.
  - `tests/test_lifecycle_orchestration.py`:
    - `Lifecycle` models the work item's `WORKFLOW_STATE.json` entry, advances it as each
      Workflow writer does, and returns each command's scripted effect in that command's commit
      order: checkpoint commits with their trailers, the step-2 state-only commit, the
      generation-record commit `T` with its trailers (plus `Workflow-Supersedes` for
      `same_content`), then the author files and a manifest whose `generation_head` is `T`, and
      the review-stage writes left uncommitted.
    - Every scenario runs through the real `cli.main` (`run`, `step`, `explain`, `resume`), so
      through the real `execute_step`, verification and next decision. Only `identity.pin`/
      `identity.current` are patched, to a pinned identity whose origin checkout declares the
      same generation, and the Workflow Manager is the offline stub.
    - The scenarios, as numbered in the plan:
      1. `run` from `IMPLEMENTING` with two checkpoints: six workers (two checkpoints, the final
         pass, a local `REVISE`, the remediation round, a local `APPROVE`), each `FINISHED`, then
         the manual gate (exit 10). The job records show each role, `claude-opus-5-5`/`xhigh`
         and single-agent where the table says so. The remediation task carries the addendum,
         the worker makes the state-only commit, a fix and `T`, and `T`'s parent records
         `APPLYING_REVIEW_FEEDBACK`. A second `run` launches nothing.
      2. From that gate: a manual `APPROVE` is ingested by one worker and stops at the
         `/approve-review implementation` gate; a manual `REVISE` loops through remediation and
         a fresh local review back to the manual gate; a manual `BLOCK` launches nothing. No
         worker is ever handed a user-only command.
      3. Ten fail-closed cases (below): each is `FAILED` with the postcondition detail. The next
         decision launches nothing. `resume` of the same job is `FAILED` as `COMPLETED` and exit
         20 (`UnreconcilableJobError`, persisted `FAILED`) as `LAUNCHED`. With only that row's
         postconditions removed (`mock.patch.dict` over `_EXPECTED_OUTCOMES_BY_KEY`) the same
         worker verifies `FINISHED`. A second test is the positive control for every row with
         the postconditions in place.
      4. Every new row (13 twice, 15, 16 both ways, 17 both ways, 18 twice), stepped one at a
         time: each record rewritten `COMPLETED` and then `LAUNCHED` reconciles `FINISHED`, and
         nothing relaunches. A `LAUNCHED` record with nothing changed is `INTERRUPTED` (exit 40).
         The I2 orphan: a child Controller is killed during an `/apply-implementation-review`;
         while its worker runs, `step` and `resume` exit 45. Released, the worker commits the
         pending write, a fix and `T`, and its generator fails. `resume` persists `FAILED` and
         exits 20, and the next `step` is CP4B's regeneration gate at `post-fix`, launching
         nothing.
      5. `PlanStageUnchangedTest` runs the unmodified stale-plan-bundle classes
         (`tests.test_job.PartialApplyPlanReviewExecuteTest`,
         `tests.test_cli.PartialApplyPlanReviewCliTest`,
         `tests.test_resume.PartialApplyPlanReviewResumeTest`) and the plan-stage golden module,
         from their own modules.
      6. Seventeen gate sub-cases (the plan-approval gates, every `"2.2"` implementation-review
         gate and sub-case, `AWAITING_PLAN_APPROVAL`, `AWAITING_FUNCTIONAL_REVIEW`): `run` exits
         10 and the fail-if-invoked fake never starts. The relaunch-bound gate is scenario 8.
      7. A literal apply worker (fix, then `T` staging only the state file, no manifest) fails,
         and the next `step` is the malformed-`T` gate naming `T` and `OPUS-R101-001`, never the
         regeneration steps. With the pending write committed first by a human, the task has no
         addendum and the same worker verifies.
      8. An apply worker that only rewrites `IMPLEMENTATION_SUMMARY.md` fails, and the next
         `run` is the relaunch-bound gate naming the job; no second worker runs.
      9. A `same_content` round (recovered-role `T`, revision unchanged, no manifest) fails, and
         the next `step` names the regeneration at `post-fix`, never
         `/recover-implementation-provenance`.
      10. From `IMPLEMENTING` with every checkpoint `COMPLETE`, a worker making only step 2's
          state-only commit verifies, and the next `run` launches the final pass.
    - The fail-closed cases of scenario 3 and their next decision:
      - the last checkpoint completion left uncommitted, and the step-2 transition left
        uncommitted: see the deviation below;
      - the final pass leaving the manifest from before `T` (stale), a completed withdrawal
        (quarantine, no `current/`) or a `REJECTED` marker: CP4B's regeneration gate, or CP4's
        `"2.2"` `REJECTED` branch, at `implementation`;
      - the remediation round leaving the previous round's manifest, a withdrawal or a `REJECTED`
        marker: the same gates at `post-fix`;
      - a local `APPROVE` and a manual ingestion recording a ledger bound to another
        `review_content_id`: the bundle is coherent, so the next decision is the manual gate and
        the `/approve-review implementation would refuse here` gate respectively. Both launch
        nothing, but neither is a bundle gate.
    - The harness tests itself: an unscripted task changes nothing and fails (exit 92), and the
      revision tokens resolve at write time.
  - **Deviation for review.** The plan says the next `step` after every fail-closed case gates.
    That does not hold for the two uncommitted-transition cases. The working tree then records
    `SELF_REVIEWING_IMPLEMENTATION`, which has no bundle to gate on, and CP3's approved handler
    gates it only on the plan approval. So the next decision re-selects `/milestone-implement`
    (the self-review role). The test pins this through `explain`, which launches nothing. Only
    the `FAILED` record, and `run`'s exit 30, stop the loop. Closing the gap needs a new decision
    gate in `controller/decision.py`, outside CP7's files, so it is left to the review.
  - Judgment calls, where the plan text left a detail open:
    - Besides `{HEAD}`, a write resolves `{HEAD^}` and `{HEAD~N}`. The manifest's
      `reviewed_implementation_head` is `T`'s parent, and so is the reviewed head in every
      review-stage write made while `T` is `HEAD`. With `{HEAD}` alone, no script written before a
      `run` could state it.
    - An action is `{"action": ..., ...}`. The counter is JSON lines, one per invocation, which
      also records every task a worker received.
    - A pre-state that is not under test is seeded in-process with `perform_actions`, never by
      launching a worker. Scenarios 1, 2, 4 and 10 build their states through workers.
    - "Stale" is read as the final pass's manifest written before `T` and the previous round's
      manifest left after a published revision.
    - "`resume` of the same job" rewrites its `FAILED` record on disk to the `COMPLETED` or
      `LAUNCHED` shape, dropping the fields a later flush adds, as a Controller that died at that
      flush leaves it.
    - The uncommitted step-2 transition joined scenario 3 (CP2 lists it as a postcondition
      clause), because the mutation check showed the committed-phase clause otherwise went
      unexercised end to end.
    - In scenario 10, the next `run` continues through a local `APPROVE` to the manual gate.
    - Records are ordered by file modification time within one CLI call; each job's last write
      precedes the next job's first.
    - Every CLI call passes `--timeout 60`, so a broken fake can never hang the suite.
  - Verified with `python3 -m unittest tests.test_lifecycle_orchestration`: 18 tests OK in about
    3.5 s. The full suite (`python3 -m unittest discover -s tests -t .`) ran 950 tests: OK, 2
    skipped. No `tests/fake_claude.py` process was left behind (0 before, 0 after).
    `python3 tests/golden/generate_external_implementation_review_decisions.py --check` reports
    that golden as current, and the plan-stage golden test is green.
  - Mutation checks were run in a scratch copy, against `controller/`. The new file catches 22 of
    23 mutants:
    - no pending-write addendum; no malformed-`T` case; no provenance-only case; no relaunch
      bound, or no job history read for it;
    - the generator stage always naming both forms; the `REJECTED` branch 1 dropped; the
      implementation-bundle gate dropped;
    - no plan-approval gate; a local `BLOCK` not gated; manual or apply admissibility skipped;
      manual ingestion never automatic;
    - the self-review postcondition's committed-checkpoint or committed-phase clause dropped; the
      row 16 or row 17 content clause dropped; verification ignoring postconditions;
    - the self-review role made multi-agent;
    - `resume` verifying every `COMPLETED` record, reconciling every `LAUNCHED` one
      `INTERRUPTED`, or not persisting an unreconcilable one `FAILED`.

    The survivor drops row 18's "a new generation ran" clause. Only a phase flip with no
    generation discriminates it, and CP2's `test_a_phase_flip_with_no_generation_fails_the_postcondition`
    (with its resume twin) catches it: 3 failures in `tests.test_job_validation tests.test_resume`.
- **CP8-CP9 -- pending**, in registry order.
