# Controller protocol-2.2 compatibility (Revision 2)

Work item: `workflow-controller-protocol-2-2-compatibility`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default, activated at commit `21a304d`)
Base commit: `21a304d50a8bb9c08e465e78a4636e353e16b5e9`

## Goal

Make Controller Generation 1 (`controller/`) correctly understand and orchestrate
Workflow protocol `2.2`'s split implementation-review lifecycle
(`AWAITING_LOCAL_IMPLEMENTATION_REVIEW` -> `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
-> the reused terminal `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`), while continuing to
drive `"1"`/`"2.1"`-governed work items exactly as it does today. Dispatch is by each
work item's own immutable `governing_workflow_version`, never by the repository's
`default_workflow_version` alone.

Until this milestone reaches `MILESTONE_COMPLETE`, this work item -- and any other
milestone in this repository -- continues to be orchestrated through normal manual
Workflow sessions, not through Controller. The repository's `default_workflow_version`
is already `"2.2"` (commit `21a304d`), and, as this plan's own investigation confirms
below, that activation alone already makes Controller crash the moment it tries to
automatically drive *any* brand-new work item -- not only one that reaches the
implementation-review split.

## Non-goals

Explicitly out of scope for this milestone (carried over verbatim from the request that
scoped it): model/provider abstraction, Codex support, dynamic model routing,
concurrency, orchestrator-model escalation, usage-aware scheduling, and any Controller
Generation 2 work. Nothing in this plan touches those areas.

## Investigation: what the installed Workflow 2.5.1 actually requires

Per this command's own instruction to derive phase semantics from the installed
contracts/scripts/tests, not from phase names alone, every claim below is cited against
`docs/ai-workflow/MILESTONE_WORKFLOW.md` and `scripts/workflow_state.py` (the frozen
Workflow release this repository carries).

- `docs/ai-workflow/MILESTONE_WORKFLOW.md:324-364` (`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`,
  `"2.2"`-only): entered only via `bundle_generation_target_phase(stage,
  governing_workflow_version)` (`scripts/workflow_state.py:11163-11192`), which returns
  this phase for `"2.2"` and the pre-existing `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
  for `"1"`/`"2.1"`, byte-for-byte unchanged. `/review-implementation` is the
  authoritative `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage writer here for a `"2.2"` item
  (advisory-only, as before, for `"1"`/`"2.1"`).
- `docs/ai-workflow/MILESTONE_WORKFLOW.md:366-397` (`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`,
  `"2.2"`-only): entered only once the `implementation_review_stages` ledger records a
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` `APPROVE` against the current implementation-stage
  `review_content_id`. `/record-manual-implementation-review` is the sole writer; a
  `Status: BLOCK` verdict pins the item here until a human resolves it.
- `docs/ai-workflow/MILESTONE_WORKFLOW.md:399-423` (verdict/state transition table,
  `"2.2"`-only pair): `APPROVE`/`REVISE`/`BLOCK` at each of the two states, with
  `/review-implementation` / `/record-manual-implementation-review` as the sole writers
  -- no Controller-driven command ever writes either ledger entry.
- `docs/ai-workflow/MILESTONE_WORKFLOW.md:425-434`: `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
  is **reused** as the terminal "ready for approval" phase once both `"2.2"` stages
  record `APPROVE` -- no new terminal phase name is introduced, and a `"1"`/`"2.1"` item's
  entry condition to this same phase name is exactly today's, unchanged.
- `docs/ai-workflow/MILESTONE_WORKFLOW.md:466-487` (`AWAITING_TECHNICAL_APPROVAL`, a
  vocabulary state, never persisted): for a `"2.2"` item, `technical_approval_gate_reachable`
  (`scripts/workflow_state.py:10420-10492`) additionally requires both
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` and `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
  recorded `APPROVE` against the current `review_content_id` -- enforced entirely inside
  `/approve-review implementation`, a user-only command Controller never selects (see
  below); nothing here needs a Controller-side change.
- `docs/ai-workflow/MILESTONE_WORKFLOW.md:176-278` (two-stage **plan**-review protocol):
  `TWO_STAGE_PLAN_REVIEW_VERSIONS = {"2.1", "2.2"}` (`scripts/workflow_state.py:302`) --
  widened from a bare `"2.1"` literal at `workflow-2.5.0`. The plan-review lifecycle
  (`PLANNING` -> `AWAITING_LOCAL_PLAN_REVIEW` -> `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` ->
  `AWAITING_PLAN_APPROVAL`, and `REVISING_PLAN`'s own re-entry into
  `AWAITING_LOCAL_PLAN_REVIEW`) is **identical** for `"2.1"` and `"2.2"` -- "only the
  downstream two-stage-vs-single-stage plan review that follows it still branches on
  governing version, unchanged" (`MILESTONE_WORKFLOW.md:53-55`).

## Investigation: the real gap in Controller (not the one first assumed)

The originating investigation described the gap as "`controller/job.py`'s
expected-outcome/decision machinery does not yet contain the 2.2-specific
implementation-review phase rows." Reading the actual source shows this is imprecise in
a way that changes this milestone's scope, and the imprecise version would have led to
the wrong fix:

1. **`controller/decision.py` and `controller/evidence.py` already fully implement the
   2.2 implementation-review classification**, added ahead of this milestone (their own
   comments say "revision 64"):
   - `KNOWN_PHASES` already lists both new phases (`decision.py:139-140`).
   - `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` is classified `DECLINED_PHASES`
     (automation-safe, Generation 1 reports rather than launches `/review-implementation`;
     `decision.py:210-213,224-228`).
   - `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` is classified `GATE_REPORT_PHASES`
     (a genuine human gate; `decision.py:216,753-760`).
   - `evidence.py:767-849` (`_decide_awaiting_manual_external_implementation_review`)
     already reads `REVIEW_FEEDBACK.md`'s `Reviewer role`/`Status` fields correctly,
     distinguishing "no feedback yet" / `Status: BLOCK` / a verdict on file, and never
     selects the user-only `/approve-review` path itself.
   - `evidence.py:713-764` (`_decide_awaiting_external_implementation_review`, the reused
     terminal phase) is already version-blind, matching
     `MILESTONE_WORKFLOW.md`'s own "byte-for-byte" reuse claim.
   - This is already unit-tested: `tests/test_decision.py`'s `Revision64PhaseWideningTest`
     (`test_decision.py:474`) decides both phases with an explicit
     `governing_workflow_version="2.2"` fixture, and `tests/test_evidence.py`'s
     `AwaitingManualExternalImplementationReviewTest` (`test_evidence.py:583`) exercises
     `evidence.decide()` end to end for that phase, though not yet exhaustively over
     every sub-case (CP4 below closes what remains).
   - Since both phases are non-automatic (`declined=True` or a `HumanGate`), `job.py`'s
     `execute_step` never looks either of them up in `EXPECTED_OUTCOMES` at all --
     `_expected_outcome_for` (`job.py:792-813`) is called only for `decision.automatic ==
     True` ("LAUNCHED-only addition", `job.py:788`). There is no missing row for this
     milestone to add here.
2. **The real, proven gap is on the plan-review side**, and it blocks a `"2.2"` item
   from being driven at all -- long before it could ever reach implementation review.
   `controller/job.py`'s `EXPECTED_OUTCOMES` table (`job.py:483-554`) has zero `"2.2"`
   literal (confirmed: `grep -n '"2\.2"' controller/job.py` returns nothing). Its four
   plan-review rows that key on governing version --
   `("PLANNING", "2.1", "/milestone-plan")`, `("AWAITING_LOCAL_PLAN_REVIEW", "2.1",
   "/review-plan")`, `("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.1",
   "/record-manual-plan-review")`, `("REVISING_PLAN", "2.1", "/apply-plan-review")` --
   have no `"2.2"` counterpart, even though all four phases are reached by a `"2.2"` item
   via the *identical*, version-blind CP4 dispatch (`decision.py:678-687`, `_DISPATCH`)
   and CP4B evidence engine (`evidence.py:944-950`) that already drive a `"2.1"` item
   through them, and even though these four are **automatic** (`decision.automatic ==
   True`), so `job.py` *does* look them up. `_expected_outcome_for` (`job.py:792-813`)
   raises `AssertionError` on a miss -- so **any `"2.2"`-governed work item Controller
   tries to drive automatically through plan review crashes today**, at whichever of the
   four rows it first reaches. A brand-new work item does *not* reach that crash at the
   very first step: `job.py`'s row 7 (`NoWorkItemYet`, `from_phase=NO_PHASE,
   governing_version=None`) bootstraps it directly to `AWAITING_LOCAL_PLAN_REVIEW`, not
   through a `("PLANNING", "2.2", "/milestone-plan")` lookup at all -- measured, through
   the real call path: a fresh repository's first `controller step` succeeds
   (`lookup (NO_PHASE, None, /milestone-plan) -> OK, to_any_of=['AWAITING_LOCAL_PLAN_REVIEW']`)
   and its **second** step is the one that raises
   (`AssertionError: decide() produced an automatic action with no known expected
   transition: phase='AWAITING_LOCAL_PLAN_REVIEW' ...`). So the crash a fresh `"2.2"`
   repository actually hits lands on Controller's second step, at
   `("AWAITING_LOCAL_PLAN_REVIEW", "2.2", "/review-plan")`, not on `PLANNING`. The
   `PLANNING` row is still required, though: `default_work_item`
   (`scripts/workflow_state.py:7604,7630`) does create entries at `PLANNING` directly
   (e.g. a resumed mid-flight item, or one constructed outside the row-7 bootstrap path),
   so a `"2.2"` item is genuinely reachable there and would crash identically at that row
   if it were missing. Since this repository's own `default_workflow_version` is now
   `"2.2"` (commit `21a304d`), this is not hypothetical either way: the next work item
   Controller creates or resumes here crashes at whichever of the four rows it reaches
   first. This is CP1's fix.
3. `controller/target_state.py` already surfaces `WorkItemView.governing_workflow_version`
   (`target_state.py:138,304`) and `WorkflowSnapshot.default_workflow_version`
   (`target_state.py:170,174-187,440`) generically, as opaque strings -- no change needed
   for either. `supported_versions` is not read anywhere in `controller/` today
   (confirmed by `grep -rn "supported_versions" controller/`); this milestone's scope
   asks Controller's target/state discovery to surface it too (parallel to the other two
   fields it already surfaces), so CP3 adds `WorkflowSnapshot.supported_versions`,
   read the same fail-soft way `default_workflow_version` already is. This stays purely
   diagnostic, exactly like `default_workflow_version` already is today (that field is
   read into `WorkflowSnapshot` but is not yet consumed by any decision, gate, or CLI
   print path either -- confirmed by `grep -rn "default_workflow_version"
   controller/cli.py controller/worker.py` returning nothing): Workflow's own
   `validate_governing_version` (`scripts/workflow_state.py:7007-7015`) remains the sole
   authority over version admissibility, and Controller does not duplicate that check.
4. `controller/worker.py`, `controller/managed_repo.py` and `controller/cli.py` contain
   no phase-name or governing-version branches at all (confirmed by grep) -- they are
   driven entirely off `decision.py`'s `Decision` object and need no change.
5. `docs/ai-workflow/registry/workflow-controller-protocol-2-2-compatibility-artifacts.json`
   (generated by `workflow_state.generate_artifacts_declarations(..., work_item_type=
   "product")`) already carries `controller/` and `tests/` as `implementation_stage.
   protected_prefixes` and `pyproject.toml` as a `protected_paths` entry, matching the
   two prior Controller work items' own deliverable tree -- the default template needs
   no manual correction for this milestone; `SELF_REVIEWING_PLAN` confirmed this fits.

**Conclusion**: this milestone is mostly a test-and-verification pass over already-correct
code, plus one concrete production fix (CP1) and one small, parallel surfacing addition
(CP3).

## Checkpoints

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Add governing_workflow_version "2.2" rows to job.py's EXPECTED_OUTCOMES table for the four plan-stage automatic phases, closing the uncaught AssertionError a 2.2-governed item hits today | - | 3 | 1 |
| CP2 | Regression-test job.py's new 2.2 plan-stage rows on both the execute-time and resume/reconciliation paths, and update the stale seven-row cardinality assertion | CP1 | 2 | 1 |
| CP3 | Surface WORKFLOW_CONFIG.json's supported_versions in target_state.py's WorkflowSnapshot, with regression coverage for default_workflow_version and a work item's governing_workflow_version already reading "2.2" correctly | - | 2 | 1 |
| CP4 | Pin, with discriminating regression tests, that decision.py/evidence.py already classify both 2.2 implementation-review phases correctly and that a 2.1 fixture's single-stage path is unchanged | - | 3 | 1 |
| CP5 | Disposable-repository integration coverage of a genuine 2.2-governed work item through the split implementation-review lifecycle | CP1, CP3, CP4 | 4 | 2 |
| CP6 | Full verification: Controller suite, all frozen Workflow conformance suites (scripts/*_test.py), and one live-worker run of the new disposable-repo test | CP1, CP2, CP3, CP4, CP5 | 2 | 1 |

### CP1 -- the fix

Add four `ExpectedOutcome` rows to `controller/job.py`'s `EXPECTED_OUTCOMES` tuple
(`job.py:483-554`), each with `governing_version="2.2"` and otherwise **byte-identical**
to its existing `"2.1"` counterpart (same `to_any_of`, `predicate`, `predicate_inputs`,
`writer_calls`) -- correct precisely because `MILESTONE_WORKFLOW.md:53-55` states the
two-stage plan-review protocol does not branch on `"2.1"` vs. `"2.2"` at all:

- `("PLANNING", "2.2", "/milestone-plan")` -> `{"AWAITING_LOCAL_PLAN_REVIEW"}`
- `("AWAITING_LOCAL_PLAN_REVIEW", "2.2", "/review-plan")` ->
  `{"AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW"}`
  (same `_predicate_row3_block_feedback_current` predicate)
- `("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.2", "/record-manual-plan-review")` ->
  `{"AWAITING_PLAN_APPROVAL", "REVISING_PLAN"}`
- `("REVISING_PLAN", "2.2", "/apply-plan-review")` -> `{"AWAITING_LOCAL_PLAN_REVIEW"}`

`EXPECTED_OUTCOMES` grows from seven rows to eleven. Update every row-count-dependent
comment/docstring this changes (confirmed by `grep -n "seven\|six-triple" job.py`, which
returns nine hits; excluding the **four** unrelated ones that do not describe this table
-- `job.py:113`, `:203`, `:744` (all "...seventeen command files...") and `job.py:951`
("The remaining seven members of the closed, ten-member enumeration")): `job.py:480`
("The seven rows..."),
`job.py:796` ("these seven triples"), `job.py:878` ("own seven rows"), `job.py:1684`
("Generation 1's own seven automatic actions"), `job.py:1819` ("Generation 1's seven
rows"). No other production file changes.

**Files**: `controller/job.py`.

**Tests**: the existing structural properties in `tests/test_job_validation.py`
(`property_table_violations`, `property_record_completeness_violations`,
`property_declaration_against_artifact_violations`) must still pass unchanged against
the eleven-row table -- they are governing-version-agnostic already, so the four new
rows exercise them for free; CP2 owns the row-count assertion update itself.

### CP2 -- prove it, on both the execute and resume paths, and both ways

Positive, execute-time: add `"2.2"` analogues of `tests/test_job.py`'s existing `"2.1"`
plan-review `execute_step` tests (the file's `_build_target` fixture already defaults
`governing_workflow_version="2.1"` and takes it as a parameter, so a `"2.2"` variant is a
small fixture change), covering all four new rows' real transitions end to end
(`PLANNING` -> `AWAITING_LOCAL_PLAN_REVIEW`; the `AWAITING_LOCAL_PLAN_REVIEW` ->
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` / `REVISING_PLAN` / (BLOCK) `AWAITING_LOCAL_PLAN_REVIEW`
split; `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` -> `AWAITING_PLAN_APPROVAL` / `REVISING_PLAN`;
`REVISING_PLAN` -> `AWAITING_LOCAL_PLAN_REVIEW`).

Positive, resume path: `job.py`'s `resume` (CP7's durable-resume reconciliation) reads
`EXPECTED_OUTCOMES` through the same `_expected_outcome_for` lookup, via a job record's
own persisted `(phase, governing_workflow_version, command)` triple
(`job.py:1236,1815,1824`), for a `PLANNED`/`LAUNCHED`/`COMPLETED` record left mid-flight.
Add a `tests/test_resume.py` case that writes such a record for a `"2.2"` work item at
one of the four new rows (e.g. a `LAUNCHED` `PLANNING` record) and asserts `resume`
reconciles it correctly (`FINISHED` on an observed `AWAITING_LOCAL_PLAN_REVIEW`) rather
than raising -- the second, independent call site the same missing-row gap could have
hit even after CP1's `execute_step`-only fix, if `resume` had needed its own copy.

Negative, permanent regression proof (this milestone's own "demonstrate the pre-fix
Controller behavior fails," made durable rather than a one-time git-history fact):
`_expected_outcome_for` takes no table argument -- it reads the module-level
`_EXPECTED_OUTCOMES_BY_KEY` dict directly (`job.py:556-558,805-806`) -- so the test uses
`unittest.mock.patch.dict` to shrink that dict down to only the seven entries that
existed before CP1 (i.e. excludes every entry with `governing_version == "2.2"`) and
asserts that calling `job._expected_outcome_for("PLANNING", "2.2", <a /milestone-plan
Decision>)` against the patched dict raises `AssertionError` -- the exact crash shape
Controller hits today, reproduced from data rather than from reverting a commit, so it
keeps proving the fix is load-bearing even as the table grows further in the future.

Finally, rename `tests/test_job_validation.py`'s `ExpectedOutcomesTableStructureTest.
test_seven_rows` to `test_eleven_rows` (assert `len(job.EXPECTED_OUTCOMES) == 11`) and
update that class's own docstring to describe the four added `"2.2"` rows.

**Files**: `tests/test_job.py`, `tests/test_resume.py`, `tests/test_job_validation.py`.

**Tests**: this checkpoint *is* its own test addition; no separate follow-on file.

### CP3 -- surface supported_versions, verify the rest

Add `WorkflowSnapshot.supported_versions: tuple[str, ...] | None` to
`controller/target_state.py`, populated by a new `_read_supported_versions(root)` helper
mirroring `_read_default_workflow_version`'s existing fail-soft contract exactly (`None`
if `WORKFLOW_CONFIG.json` is missing/unparseable/not a dict, or if `supported_versions`
is absent or not a list of strings; otherwise the tuple of strings, in file order) --
read-only diagnostic context, exactly as `default_workflow_version` already is (neither
field is consumed by any decision/gate today; Workflow's own
`workflow_state.validate_governing_version` remains the sole admissibility authority,
never duplicated here). Wire it into `read()` alongside the existing
`default_workflow_version=...` construction.

Add `tests/test_target_state.py` coverage, through the real `target_state.read()` path
(not a synthetic `WorkItemView` construction) against a fixture target repository the
test itself writes a `WORKFLOW_CONFIG.json` into -- `target_state.read()` reads the
**target** repository's config, so pointing assertions at this repository's own file
would break the test the next time `docs/ai-workflow/WORKFLOW_CONFIG.json` changes here
-- confirming: `WorkflowSnapshot.default_workflow_version == "2.2"` and
`WorkflowSnapshot.supported_versions == ("1", "2.1", "2.2")` when the fixture declares
them (values chosen to match this repository's own real post-`21a304d` file, but read
from the fixture, not from it), and `WorkItemView.governing_workflow_version == "2.2"`
when a work-item entry declares it.

**Files**: `controller/target_state.py`, `tests/test_target_state.py`.

**Tests**: this checkpoint *is* its own test addition.

### CP4 -- pin the already-implemented 2.2 classification, and the 2.1 parity guarantee

`decision.py`/`evidence.py` already implement `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` /
`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` / the reused
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` correctly (investigation above). This
checkpoint is an audit-and-harden pass, not new production logic. Read
`tests/test_decision.py`'s and `tests/test_evidence.py`'s existing coverage of these
three phases first: `AwaitingManualExternalImplementationReviewTest`
(`tests/test_evidence.py:583`) already exercises
`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, against a real, fixture-built
`governing_workflow_version="2.2"` `WorkItemView` and real on-disk
`REVIEW_FEEDBACK.md` content, with no current-round feedback (`:604`
`test_no_feedback_names_handing_the_bundle_to_a_reviewer`), a wrong `Reviewer role:` in
two forms (`:612` `test_local_role_feedback_does_not_satisfy_the_arrival_test`, `:623`
`test_lowercase_role_is_not_accepted_unlike_the_plan_stage`), `Status: BLOCK` (`:639`
`test_block_is_a_gate_naming_explicit_resolution`), and a verdict on file with `Status:
APPROVE` (`:651`
`test_verdict_on_file_names_record_manual_implementation_review_and_never_launches`).
Only `Status: REVISE` on file is genuinely uncovered there -- add that one sub-case,
against the same fixture shape the existing class already uses.

Add one more test proving `_decide_awaiting_external_implementation_review` (the reused
terminal phase) reports identically for a `"2.2"` item with both ledger stages
`APPROVE`'d and for a `"2.1"`/`"1"` item in the same bundle state -- the concrete
regression guard for `MILESTONE_WORKFLOW.md:425-434`'s "byte-for-byte" reuse claim, and
genuinely new coverage: `AwaitingExternalImplementationReviewTest`
(`tests/test_evidence.py:529`) has no `governing_workflow_version="2.2"` fixture
anywhere today.

Together these close every sub-case the Controller can actually **observe** in
`MILESTONE_WORKFLOW.md`'s `"2.2"` verdict/state transition table
(`MILESTONE_WORKFLOW.md:399-410`) -- not literally every row in that table. That table
has six rows, and its three `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` rows have no
Controller-observable distinction at all: that phase is `DECLINED_PHASES`
(`decision.py:210-213`) with no `_EVIDENCE_HANDLERS` entry, so Controller's behavior
there is verdict-independent by construction and no test can discriminate between them.
None is added for that reason.

Close this checkpoint with the explicit `"1"`/`"2.1"` parity half: a regression pass
proving `KNOWN_PHASES` membership, `DECLINED_PHASES`/`GATE_REPORT_PHASES`
classification, and (once CP1 lands) `job.py`'s automatic plan-review dispatch are
unaffected by this milestone -- a `"1"`/`"2.1"` item never reports or enters
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`/`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`,
and its own `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`/`AWAITING_LOCAL_PLAN_REVIEW`/
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` handling is identical to its pre-milestone
behavior.

**Files**: `tests/test_decision.py`, `tests/test_evidence.py`.

**Tests**: this checkpoint *is* its own test addition.

### CP5 -- disposable-repository integration coverage for the split lifecycle

Add a new test class to `tests/test_integration_disposable_repo.py` that builds a
genuine `"2.2"`-governed work item in a disposable target repository -- real
Git-committed source tree, a real `WORKFLOW_STATE.json` entry (via
`tests/fixtures.write_workflow_state`), real registry/mapping/artifacts-declaration
files, and real `.ai-review/` bundle/feedback files matching the exact
`MANIFEST.md`/`REVIEW_FEEDBACK.md` field formats `scripts/workflow_state.py`'s own
readers expect (via `tests/fixtures.write_manifest`/`build_manifest_text`/
`write_review_feedback`/`build_review_feedback_text`) -- seeded in turn at:

1. `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, no ledger yet -- `controller step`/`explain`
   reports `declined=True`, names `/review-implementation`, launches no worker.
2. `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, no current-round feedback -- reports
   the gate, names `/record-manual-implementation-review`.
3. Same phase, `Status: BLOCK` on file -- reports the BLOCK gate; still declines to act.
4. Same phase, `Status: APPROVE` on file but not yet ingested (phase unchanged, ledger
   not yet updated) -- reports "a verdict is on file; run
   /record-manual-implementation-review," never invents the approval itself.
5. The reused terminal `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `APPROVE` on file --
   reports the user-only `/approve-review implementation` gate, exactly as for a
   `"2.1"`/`"1"` item.

A `controller resume` pass across the same five seeded states must not crash or
misclassify at any of them, and at no point may Controller select
`/approve-review`/`/accept-milestone` itself (the human-only-approval boundary).

Separately, extend the existing **live-Claude** `DisposableRepoRealWorkflowActionTest`
fixture (`tests/test_integration_disposable_repo.py:402-511`, gated on a real
`CLAUDE_BIN` exactly as today) with a sibling test that seeds `WORKFLOW_CONFIG.json`'s
`default_workflow_version` as `"2.2"` **and** `supported_versions` to include `"2.2"`.
Both keys are required: the fixture installs the `2.5.1` release
(`controller/managed_repo.py:89,105`, `VALIDATED_WORKFLOW_RELEASES = frozenset({"2.5.1"})`),
whose own bootstrap template
(`workflow-manager/distribution/workflow/2.5.1/templates/docs/ai-workflow/WORKFLOW_CONFIG.json`)
ships `"supported_versions": ["1", "2.1"]` with no `"2.2"` -- seeding only the default
leaves `workflow_state.validate_governing_version`
(`scripts/workflow_state.py:7007-7015`) refusing the work item at creation, before
Controller ever gets to dispatch it (this repository's own activation commit `21a304d`
had to add `"2.2"` to `supported_versions` by hand for exactly this reason).

Run **two** `controller step` calls against the seeded repository, not one:

1. The first is row 7's `NoWorkItemYet` bootstrap (`from_phase=NO_PHASE,
   governing_version=None`, unaffected by CP1) -- it creates the new work item and lands
   it at `AWAITING_LOCAL_PLAN_REVIEW`. Assert `governing_workflow_version == "2.2"` and
   the phase, but do not treat this step as proving CP1: it passes identically with or
   without CP1's fix, since it never looks up a `"2.2"` row at all.
2. The second step is the discriminating one: run `controller step` again on the same
   work item, driving it through `("AWAITING_LOCAL_PLAN_REVIEW", "2.2", "/review-plan")`
   -- the exact row CP1 adds. Before CP1 this raises the uncaught `AssertionError`;
   after CP1 it launches a real `/review-plan` session and the work item durably
   reconciles to one of that row's `to_any_of` outcomes
   (`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` / `REVISING_PLAN` /
   `AWAITING_LOCAL_PLAN_REVIEW`).

This is the end-to-end, live-Claude-driven proof that CP1's fix actually unblocks real
orchestration, not only the unit-level `execute_step` calls CP2 exercises directly.

The implementation-review states above are deliberately seeded rather than driven by a
second live Claude run all the way through `IMPLEMENTING`: both are non-automatic
(`declined`/`gate`), so Controller never launches a worker at either one regardless of
how the target repository reached them -- a live, multi-session `IMPLEMENTING` ->
`SELF_REVIEWING_IMPLEMENTATION` -> local-review -> manual-review run would exercise real
Workflow/Claude review content this milestone has no need to re-verify, at a cost and
duration wholly disproportionate to what Controller itself needs proven here (that it
reports and routes correctly once at each state). This choice is recorded here so a
reviewer does not read the absence of a second live run as an oversight.

**Files**: `tests/test_integration_disposable_repo.py`, `tests/fixtures.py` (only if a
small helper genuinely earns its keep -- reuse the existing builders first).

**Tests**: this checkpoint *is* its own test addition.

### CP6 -- full verification

This repository has no `pytest` installed (`python3 -m pytest --version` ->
`No module named pytest`; `pyproject.toml` declares `dependencies = []` with no pytest
dependency or `[tool.pytest.ini_options]`) and uses `unittest` everywhere else
(`README.md:109`, the two prior Controller plans, and this plan's own `TEST_RESULTS.md`
baseline). Run, and record the result of, in that idiom:

1. The Controller's own suite: `python3 -m unittest discover -s tests -t .` from the
   repository root.
2. The **seven** frozen Workflow conformance suites under `scripts/` that are green at
   this plan's own base commit -- each invoked directly as `python3 scripts/<name>_test.py`
   (note: `unittest discover`'s default `test*.py` pattern does not match `*_test.py`,
   so `discover` alone silently collects none of them): `workflow_acceptance_matrix_test.py`,
   `workflow_fingerprint_generalization_test.py`, `workflow_fingerprint_test.py`,
   `workflow_integration_test.py`, `workflow_state_completion_obligations_test.py`,
   `workflow_state_test.py`, `workflow_test_harness_test.py`. Re-list at implementation
   time rather than trusting this count, since `scripts/` is not this milestone's own
   file set and may have grown -- but re-list from the suites that are green at base
   (per `find . -maxdepth 2 -iname "*_test.py"` minus the two excluded below), not from
   a bare `find`.

`scripts/workflow_fingerprint_demo_test.py` and `scripts/workflow_state_demo_test.py`
are excluded from this checkpoint's exit condition entirely, not merely allowed to fail:
both already fail at this plan's own base commit, measured this session --
`workflow_fingerprint_demo_test.py` (`Ran 15, FAILED, errors=5, skipped=4`) and
`workflow_state_demo_test.py` (`Ran 47, FAILED, failures=11, errors=17`) -- for reasons
entirely unrelated to this milestone: they assert against the **Workflow** repository's
own real artifacts (e.g.
`compute_review_content_id_plan_stage_at_commit_for_work_item(repo_root,
"workflow-v2-1-core", ...)` at `workflow_fingerprint_demo_test.py:147`, and
`.ai-review/current` bundle content), none of which exists in this repository.
`scripts/` is this work item's own declared `implementation_stage.excluded_prefixes`
entry, and repairing these two frozen, unrelated upstream suites is out of scope per
Non-goals; this checkpoint must not narrow the list silently or "fix" them to reach
zero failures -- it must name them as excluded, with this reason, wherever the suite set
is stated.

Zero failures across (1) and the seven suites named in (2) is this checkpoint's exit
condition.

Additionally, with a real `CLAUDE_BIN` available, run CP5's new disposable-repo test
module at least once end to end (including its live-Claude sibling test) as a final,
concrete confirmation that the seeded-state assertions and the live-orchestration path
agree -- not only that each passes in isolation.

**Files**: none (verification-only checkpoint).

**Tests**: none new; this checkpoint runs everything CP1-CP5 added.

## Open decisions (`docs/TECHNICAL_DECISIONS.md`)

`docs/TECHNICAL_DECISIONS.md` does not exist in this repository (confirmed: `find . -iname
"TECHNICAL_DECISIONS*"` returns nothing), nor do `docs/DOMAIN_GLOSSARY.md`/
`docs/UX_FLOWS.md` -- these are generic references in the milestone-plan command text
that do not apply to this workflow-tooling repository. Nothing to check or finalize
here.

## Verification

Per checkpoint: run the narrowest relevant subset, in this repository's existing
`unittest` idiom (see CP6's own section for why -- there is no `pytest` here):
`python3 -m unittest tests.test_job tests.test_job_validation tests.test_resume` for
CP1/CP2, `python3 -m unittest tests.test_target_state` for CP3, `python3 -m unittest
tests.test_decision tests.test_evidence` for CP4, `python3 -m unittest
tests.test_integration_disposable_repo` for CP5. CP6 is the full-suite run itself (the
Controller suite plus the seven green `scripts/*_test.py` suites), from the repository
root, before proceeding to implementation review.

## Migration / data-integrity notes

None. This milestone adds no new `WORKFLOW_STATE.json` field, no new
`WORKFLOW_CONFIG.json` field, and no new persisted Controller record shape: CP1's four
new `job.py` rows are in-process table data, not a durable schema; CP3's
`WorkflowSnapshot.supported_versions` field is read-only, derived fresh from
`WORKFLOW_CONFIG.json` on every `read()` call, never itself persisted. No existing job
record, work item, or Workflow state file requires backfill or migration for this
milestone to be safe to land, and no existing test assertion is weakened or removed to
accommodate it (CP1-CP5 are additive; CP2's two literal count updates --
`test_seven_rows` -> `test_eleven_rows` and its docstring -- are the only edits to a
pre-existing assertion, and both track a real, correct cardinality change rather than
relaxing coverage).
