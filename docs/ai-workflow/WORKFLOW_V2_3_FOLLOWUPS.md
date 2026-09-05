# Workflow v2.3 — Deferred Follow-ups

This file records concrete workflow defects and maintenance items discovered while developing or operating Workflow v2.3 that are intentionally deferred so they do not expand the active v2.3 milestone.

The purpose of this file is to prevent known issues from being forgotten while preserving a strict scope boundary:

- Workflow v2.3 remains focused on reviewer-command ergonomics.
- Deferred items here are not part of the active v2.3 acceptance criteria unless explicitly promoted into the milestone.
- After v2.3 is complete, fix only the concrete correctness/tooling defects that materially affect normal workflow operation or the upcoming Workflow Manager / Bootstrapper work.
- Ergonomic improvements, broader refactors, and lifecycle redesigns should remain deferred to later workflow versions unless they become necessary.

---

## 1. `/approve-review plan` conditional fifth-member staging bug

### Status

Closed — fixed by `workflow-v2-3-followups` `CP1` (merged
`.claude/commands/approve-review.md` plan-stage steps 5 and 6.1 into one
guarded window, new ordinary step label `"step-5-stage-and-pin"`; see
`docs/ai-workflow/requirements/workflow-v2-3-followups-ledger.md`'s `CP1`
section for full implementation evidence).

### Discovered while

Approving the `workflow-v2-3` plan after both local and manual external plan reviews had approved the current revision.

### Affected area

- `.claude/commands/approve-review.md`
- `scripts/workflow_state.py`
- plan-approval commit staging
- conditional fifth-member handling
- approval transaction/journal path

### Problem

`/approve-review plan` documents a staging sequence that becomes impossible when the conditional fifth plan-approval commit member applies.

The intended plan-approval commit set can contain:

1. plan document;
2. registry;
3. requirements mapping;
4. `WORKFLOW_STATE.json`;
5. conditional artifacts declaration, when applicable.

For `workflow-v2-3`, the conditional fifth member was:

```text
docs/ai-workflow/registry/workflow-v2-3-artifacts.json
````

The documented `/approve-review` sequence stages the conditional fifth member first, then later calls:

```text
stage_plan_approval_commit_paths(...)
```

again for the remaining ordinary non-state commit members.

However, `stage_plan_approval_commit_paths(...)` has a precondition requiring the Git index to be empty before it begins staging.

Therefore:

```text
stage fifth member
        ↓
index is now non-empty
        ↓
stage_plan_approval_commit_paths(...)
        ↓
DirtyIndexBeforeStagingError
```

This means the documented two-call sequence cannot succeed whenever the conditional fifth member actually participates in the approval commit.

### Observed failure

The approval attempt reached the staging sequence with all prior guards passing:

* current phase was correct;
* `bundle_id` recomputed successfully and matched the reviewed bundle;
* `review_content_id` recomputed successfully and matched the reviewed content;
* user confirmation was valid;
* approval basis resolved to `EXTERNAL_APPROVE`;
* the expected five-member approval commit set resolved correctly;
* the durable approval journal opened successfully.

The first staging step staged the conditional fifth member.

The next call to `stage_plan_approval_commit_paths(...)` failed with:

```text
DirtyIndexBeforeStagingError
```

because the helper correctly detected that the index was already non-empty.

### Recovery performed

The failed attempt was rolled back cleanly.

Verified recovery state:

* no approval commit was created;
* `HEAD` remained unchanged;
* approval journal outcome was `NOT_COMMITTED`;
* approval journal was closed;
* Git index was returned to clean state;
* no files remained staged;
* working-tree contents were unchanged;
* `WORKFLOW_STATE.json` retained only the already-valid manual-plan-review ingestion state;
* work item remained at `AWAITING_PLAN_APPROVAL`.

### Safe workaround used

The approval was retried by preserving the existing helper invariants but combining the non-state staging operation.

Instead of:

```text
stage fifth member separately
↓
call stage_plan_approval_commit_paths(...) for ordinary members
```

the safe sequence is:

```text
clean index
↓
stage all non-state approval members in one guarded staging operation
↓
verify the conditional fifth member's staged blob matches its pinned SHA
↓
stage/pin WORKFLOW_STATE.json
↓
assert the exact expected staged set
↓
create the approval commit
```

The dirty-index guard itself was not weakened or bypassed.

### Why this is considered a real tooling defect

The helper's empty-index precondition is valid and useful.

The defect is in the documented command sequencing: the command asks the same helper to run after a previous step has intentionally made its precondition false.

The test suite also does not appear to exercise this exact real-world two-call sequence with the conditional fifth member staged first.

The existing test covering fifth-member staging combines the non-state members into a single staging call, which is compatible with the helper's invariant and matches the successful workaround.

### Required follow-up

After Workflow v2.3 completes:

1. Correct `/approve-review plan` so the conditional fifth member and the ordinary non-state approval members are staged through one coherent guarded operation.
2. Preserve the empty-index / dirty-index protection.
3. Preserve conditional fifth-member pinned-SHA verification.
4. Preserve the durable approval journal.
5. Preserve exact staged-set assertions before commit.
6. Preserve the current state-pin/staging order.
7. Add a regression test exercising the real conditional fifth-member path with actual Git staging.
8. Verify the no-fifth-member approval path still behaves identically.
9. Verify rollback/retry semantics remain correct when staging or commit creation fails.

### Suggested regression scenario

Create a temporary repository/work item where the plan-approval commit set contains all five members.

Exercise the same sequence the real command uses and prove:

```text
clean index
↓
resolve five-member approval set
↓
stage four non-state members together
↓
verify fifth-member staged blob against pinned SHA
↓
stage state
↓
assert exact staged set
↓
commit
↓
approval succeeds
```

Also include a negative case proving that unrelated pre-staged content still raises the existing dirty-index refusal.

### Scope decision

Do not expand Workflow v2.3 to fix this unless the bug prevents v2.3 from completing.

The current safe workaround is sufficient to finish the active milestone without weakening any approval invariant.

Fix this as a focused workflow-maintenance change immediately after v2.3.

---

## 2. `/review-implementation` authoritative feedback writeback

### Status

Closed by `workflow-v2-3-followups` `CP2`, except required follow-up #8
below (operator reference / lifecycle diagram sync), which remains
explicitly deferred: those two artifacts
(`docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` and
`docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg`) are
untracked, concurrent, out-of-scope scratch content left over from a
previous session, excluded from this item's own plan/implementation
artifacts declarations per `CLAUDE.md`'s "don't touch unrelated
working-tree changes" (`LPR-R2-I01`, round-2 local plan review of
`workflow-v2-3-followups`). Carried forward as a named prerequisite of
"before freezing the reusable workflow baseline" (see the "Follow-up
policy after v2.3" section below), not silently dropped.

### Motivation

The main purpose of `/review-implementation` is to make implementation review semi-automatic and remove the need for a large handcrafted model prompt.

During the first real `/review-implementation` remediation loop, the command correctly performed an independent review and produced a fully bound `REVIEW_FEEDBACK.md`-shaped report, but intentionally wrote nothing.

The operator then had to manually copy the report from chat into:

```text
.ai-review/feedback/REVIEW_FEEDBACK.md
```

before `/apply-implementation-review` could consume it.

This creates an unnecessary manual handoff:

```text
/review-implementation
        ↓
review appears in chat
        ↓
operator manually copies review
        ↓
REVIEW_FEEDBACK.md
        ↓
/apply-implementation-review
```

It also creates an avoidable stale-feedback failure mode: after a remediation round generates a new bundle, the old `REVIEW_FEEDBACK.md` remains on disk until the operator manually replaces it.

That behavior is consistent with the currently approved Workflow v2.3 plan, so it is not an implementation defect. It is an operator-ergonomics/design follow-up discovered by dogfooding the new command.

### Desired behavior

`/review-implementation` should follow the same operator-facing pattern as `/review-plan` for producing its review artifact:

> The reviewer command performs the review and writes the canonical review artifact itself. The operator should not have to copy the model's output from chat into `REVIEW_FEEDBACK.md` before the workflow can consume it.

This does **not** mean `/review-implementation` should inherit `/review-plan`'s plan-specific ledger or state-transition behavior.

The intended separation remains:

```text
/review-implementation
        ↓
perform independent implementation review
        ↓
write canonical REVIEW_FEEDBACK.md
        ↓
STOP

/apply-implementation-review
        ↓
validate authoritative REVIEW_FEEDBACK.md
        ↓
apply accepted findings / remediation

/approve-review implementation
        ↓
perform the technical-approval gate transition
```

### `/review-plan` as the reference operator experience

`/review-plan` is the existing reference for the desired review-artifact handoff.

It already:

1. validates the current review subject and lifecycle preconditions;
2. performs an independent review;
3. writes the canonical `REVIEW_FEEDBACK.md`;
4. binds the review to the current review identity.

Its additional state/ledger behavior is specific to the explicit `local_model_plan_review` lifecycle stage and must **not** be copied into `/review-implementation` as part of this follow-up.

### `/review-implementation`

Current behavior:

```text
/review-implementation
        ↓
perform review
        ↓
print REVIEW_FEEDBACK.md-shaped report in chat
        ↓
write nothing
        ↓
operator manually copies report
```

Desired behavior:

```text
/review-implementation
        ↓
validate current implementation-review bundle
        ↓
perform independent review
        ↓
write canonical REVIEW_FEEDBACK.md
        ↓
remain AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
        ↓
STOP
```

The command should:

1. preserve all existing bundle, `review_content_id`, provenance, phase, rejection-marker, and local-generation checks;
2. perform the same independent implementation review it performs today;
3. write the completed review directly to the canonical feedback path resolved by the workflow;
4. include the exact current binding fields required by downstream parsers;
5. replace stale feedback from an earlier implementation bundle only after the new review has passed all pre-write guards;
6. re-run any required freshness/rejection guard immediately before writing, following the same fail-closed philosophy as the existing workflow;
7. make no source, test, plan, registry, mapping, artifacts-declaration, or bundle changes;
8. make no approval;
9. make no implementation remediation;
10. make no workflow-state transition;
11. stop immediately after writing the review artifact and reporting the verdict.

`/apply-implementation-review` remains the only command that acts on implementation-review findings.

`/approve-review implementation` remains the only technical-approval gate.

### `/review-functional` is intentionally unchanged

`/review-functional` is **not part of this follow-up**.

Its current Workflow v2.3 behavior remains intentional:

```text
AWAITING_FUNCTIONAL_REVIEW
        ↓
/review-functional
        ↓
model-assisted checklist/evidence review
        ↓
report in chat only
        ↓
no file write
no state transition
        ↓
still AWAITING_FUNCTIONAL_REVIEW
```

Do not make `/review-functional` write `FUNCTIONAL_REVIEW.md` or another canonical review artifact as part of the pre-Manager / Bootstrapper work.

Do not change its relationship to manual functional testing or final user acceptance.

If a future Controller-era workflow version finds a concrete orchestration need for a persisted model-assisted functional-review artifact, design that separately with the human functional-review semantics explicitly preserved.

### Required follow-up

After Workflow v2.3 completes:

1. **Closed (`CP2`).** Treat `/review-plan` as the reference operator experience for review-artifact writeback, while preserving its plan-specific ledger/state semantics.
2. **Closed (`CP2`).** Change `/review-implementation` to write the authoritative current `REVIEW_FEEDBACK.md` directly.
3. **Closed (`CP2`).** Add pre-write and immediate-pre-write freshness/rejection checks so a review cannot overwrite feedback after its subject becomes stale during the review.
4. **Closed (`CP2`).** Define safe replacement semantics for feedback belonging to an older implementation bundle — resolved via a refusal guard (`assert_feedback_not_owned_by_other_work_item`), not relocation; see `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md`'s CP2 design (`GPT-FUP-R6-I01`, `LPR-R7-B01`).
5. **Closed (`CP2`).** Add regression coverage proving a newly written feedback artifact binds successfully through `parse_review_feedback_binding_fields` / `assert_feedback_matches_bundle`.
6. **Closed (`CP2`).** Add a regression proving stale prior feedback is replaced only by a successfully completed review of the current bundle.
7. **Closed (`CP2`).** Add negative tests proving a stale/rejected/wrong-phase implementation review writes nothing.
8. **Deferred, not closed** (see Status above): update the operator reference and lifecycle diagram so the normal `/review-implementation` path no longer contains a manual copy/paste step — blocked on `docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`/`docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg`, both untracked, out-of-scope scratch content this milestone declines to bring into scope. Carried forward to "before freezing the reusable workflow baseline."
9. **Closed (`CP2`).** Leave `/review-functional` unchanged.

### Scope decision

Do not amend the already-approved Workflow v2.3 plan solely to introduce this behavior.

Finish v2.3 according to its approved command contracts, then implement this as a focused follow-up before the reusable workflow baseline is frozen for the Workflow Manager / Bootstrapper.

This follow-up is higher priority than ordinary cosmetic ergonomics because semi-automatic implementation review is one of the main reasons `/review-implementation` exists and because removing the manual handoff directly simplifies future manager/controller orchestration.

---

## 3. Normalize review-role and review-stage identifiers to `SCREAMING_SNAKE_CASE`

### Status

Closed by `workflow-v2-3-followups` `CP3` for required follow-ups #1-#6 and
#8, and for the "tests, fixtures" and tracked-operator-documentation halves
of #7 (`scripts/workflow_state_test.py`, `scripts/workflow_integration_test.py`,
`docs/ai-workflow/MILESTONE_WORKFLOW.md`,
`docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`) — new writes use only the
canonical `LOCAL_MODEL_PLAN_REVIEW`/`MANUAL_EXTERNAL_PLAN_REVIEW` tokens,
compatibility reading of the legacy lowercase tokens is centralized in
`workflow_state.normalize_plan_review_stages`, every live non-terminal
work item's ledger (`workflow-v2-3-followups`'s own) was migrated in place
by `workflow_state.migrate_plan_review_stage_keys`, and `workflow-v2-3`'s
own terminal record was left byte-unchanged. The remaining half of #7
(the untracked `docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md` and
`docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg`) remains
explicitly deferred for the same reason item 2's own required follow-up #8
is: those two artifacts are untracked, concurrent, out-of-scope scratch
content left over from a previous session, excluded from this item's own
plan/implementation artifacts declarations per `CLAUDE.md`'s "don't touch
unrelated working-tree changes." Carried forward as a named prerequisite
of "before freezing the reusable workflow baseline" (see the "Follow-up
policy after v2.3" section below), not silently dropped.

### Motivation

Workflow lifecycle phases already use protocol-style `SCREAMING_SNAKE_CASE`, for example:

```text
AWAITING_LOCAL_PLAN_REVIEW
AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW
AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
AWAITING_FUNCTIONAL_REVIEW
MILESTONE_COMPLETE
```

Review-role / review-stage identifiers currently use a different convention, for example:

```text
local_model_plan_review
manual_external_plan_review
```

This creates an unnecessary protocol-style inconsistency. Before the workflow becomes a reusable distributed baseline, normalize persisted review-role / review-stage identifiers to the same visibly machine-oriented convention.

Target shape:

```text
LOCAL_MODEL_PLAN_REVIEW
MANUAL_EXTERNAL_PLAN_REVIEW
```

Future review-stage identifiers should follow the same convention, for example:

```text
LOCAL_MODEL_IMPLEMENTATION_REVIEW
MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW
```

if those stages are later introduced.

### Important scope boundary

This follow-up is **only a naming/protocol normalization**.

It must **not** introduce new implementation-review lifecycle states or new review-ledger stages before the Manager / Bootstrapper.

In particular, do not add as part of this follow-up:

```text
AWAITING_LOCAL_IMPLEMENTATION_REVIEW
LOCAL_MODEL_IMPLEMENTATION_REVIEW
MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW
```

as new active lifecycle semantics.

Those richer local-vs-external implementation-review distinctions are deferred until after the Workflow Manager / Bootstrapper and Controller exist, when the orchestration layer has a concrete need to distinguish which actor it is waiting for.

### Migration requirement

The existing lowercase identifiers are persisted protocol values and may appear in:

- `WORKFLOW_STATE.json`;
- review ledger entries;
- `Reviewer role:` fields;
- parser/validation functions;
- command contracts;
- tests;
- historical review artifacts;
- documentation.

Therefore this must be implemented as an explicit compatibility migration, not as a blind search-and-replace.

Required behavior:

1. new writes use only the normalized `SCREAMING_SNAKE_CASE` values;
2. existing live state is migrated deliberately where appropriate;
3. readers/parsers remain able to understand legacy lowercase values where historical artifacts or previously persisted state require it;
4. immutable historical review evidence is not rewritten solely for cosmetic normalization;
5. current-turn validation and canonical templates/documentation use the new identifiers consistently;
6. any compatibility aliasing has a clearly documented removal/deprecation policy rather than silently supporting two spellings forever.

### Required follow-up

After Workflow v2.3 completes:

1. Inventory every persisted or parsed review-role / review-stage token.
2. Define the canonical normalized token set.
3. Add compatibility parsing for legacy lowercase values where required.
4. Migrate current live workflow state to the canonical values.
5. Update reviewer-role templates and exact-token validation.
6. Update `/review-plan`, `/record-manual-plan-review`, and any other command that reads or writes these identifiers.
7. Update tests, fixtures, operator documentation, and the lifecycle/reference diagram where these tokens appear.
8. Add regression tests proving:
    - canonical new values are written;
    - legacy values remain readable where compatibility is required;
    - malformed or ambiguous values still refuse cleanly;
    - historical evidence does not need mutation to remain readable.
9. Re-run the full workflow test suite before freezing/tagging the reusable baseline.

### Scope decision

Do this before the Manager / Bootstrapper so the distribution layer does not have to standardize and ship a protocol naming convention that is already known to be inconsistent.

Do **not** combine this with a broader lifecycle redesign. The Manager / Bootstrapper should receive the same proven state machine, with cleaner canonical protocol identifiers.

The first-class local implementation-review state/ledger design remains deferred until after the Bootstrapper and Controller exist.

---

## 4. `/review-implementation` never reads the approved plan, so it cannot detect plan deviation

### Status

Closed — fixed by `workflow-v2-3-followups` `CP2` alongside item 2, exactly
as this item's own "Required follow-up" below suggested: `<bundle_dir>/PLAN.md`
and `plan_path` added to `.claude/commands/review-implementation.md` step 3's
read list, and a plan-conformance search arm added to step 5.

### Motivation

Discovered while applying round 2 implementation-review feedback
(`RI2-002`, `/apply-implementation-review workflow-v2-3`).

`.claude/commands/review-implementation.md` step 3's read list is
otherwise exhaustive — nine bundle artifacts, the required-context file
list, and prior feedback — but it names neither `<bundle_dir>/PLAN.md`
(which the generator ships unconditionally on every implementation/
post-fix bundle) nor the item's own `plan_path`. Step 5's search list
(correctness bugs, layering violations, migration/backup risk, missing
tests, usability) has no plan-conformance arm either. `/review-plan`
step 4, the natural sibling command, does read "the authoritative plan
doc (`plan_path`)" — `/review-implementation` has no equivalent
instruction.

### Observed consequence

Demonstrated by this work item's own round-1 history: round 1's Important
finding GPT-IR1-002 was found by reading the approved plan — the external
reviewer's own write-up opens "The approved revision-13 plan explicitly
required…". A reviewer executing `/review-implementation` literally, with
no independent decision to open the plan, would not have caught it. A
second-opinion command whose stated review objective (per the bundle's own
`REVIEW_REQUEST.md`) includes "match the approved plan" cannot
structurally detect plan deviation from its own instructions alone.

### Why this is a plan-stage gap, not an implementation defect

`docs/ai-workflow/WORKFLOW_V2_3_PLAN.md:2137-2140` specifies this exact
read list, with no plan document in it, and the implementation is
faithful to the approved plan. The gap originated at the plan stage and
passed both plan-review stages (`local_model_plan_review` and
`manual_external_plan_review`) without being flagged. Correcting it
inside a normal implementation round would mean amending an
already-approved plan mid-round, which `/apply-implementation-review` is
not the right place to do.

### Required follow-up

Add a plan-conformance read (`<bundle_dir>/PLAN.md` and/or the item's
`plan_path`) to `/review-implementation` step 3, and a plan-conformance
search arm to step 5, mirroring `/review-plan` step 4's existing "read the
authoritative plan doc" instruction. Consider doing this alongside item 2
above, since both touch `/review-implementation`'s contract.

### Scope decision

Not added to the "three items" mandatory pre-Manager/Bootstrapper scope
below: `/review-implementation` remains report-only and non-gating, so the
gap does not silently approve anything by itself, and this work item's own
round-2 review — run under the same unmodified read list — raised the gap
itself rather than missing another defect because of it. Fix opportunistically
after v2.3 completes, ideally alongside item 2's `/review-implementation`
rework so the command's contract is only touched once.

---

## Follow-up policy after v2.3

After Workflow v2.3 reaches `MILESTONE_COMPLETE`, review this file before beginning the Workflow Manager / Bootstrapper work.

The **pre-Manager / Bootstrapper follow-up scope is intentionally limited to three items**:

1. fix the `/approve-review plan` conditional fifth-member staging bug;
2. make `/review-implementation` write its own canonical `REVIEW_FEEDBACK.md` so the intended semi-automatic implementation-review flow does not require manual copy/paste;
3. normalize persisted review-role / review-stage identifiers to canonical `SCREAMING_SNAKE_CASE`, with backward-compatible reading where required.

These three items should be completed, regression-tested, and reviewed before the reusable workflow baseline is frozen/tagged.

Item 4 (`/review-implementation` never reads the approved plan) is deliberately not part of this mandatory three-item scope — see its own "Scope decision" above — but is a good opportunistic pairing with item 2 since both touch `/review-implementation`'s contract.

`/review-functional` is intentionally left unchanged by the pre-Manager / Bootstrapper follow-up scope.

Do not delay the Manager / Bootstrapper for:

- new local-vs-external implementation-review lifecycle states;
- `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`;
- implementation-review ledger/quorum redesign;
- controller-specific orchestration states;
- richer multi-reviewer approval semantics;
- cosmetic documentation issues;
- speculative hardening;
- unrelated operator ergonomics;
- broader workflow lifecycle redesign.

Those lifecycle/orchestration changes are explicitly deferred until after the Workflow Manager / Bootstrapper and Controller exist.

The purpose of this boundary is to fix concrete protocol/tooling issues that would otherwise be baked into the distributed baseline without reopening Workflow v2.1's proven state-machine architecture.

---

## Post-v2.3 sequence

Expected sequence:

```text
finish workflow-v2-3
        ↓
technical review
        ↓
functional review
        ↓
accept workflow-v2-3
        ↓
fix /approve-review fifth-member staging bug
        ↓
make /review-implementation write canonical REVIEW_FEEDBACK.md
        ↓
normalize review-role / review-stage identifiers to SCREAMING_SNAKE_CASE
        ↓
focused regression + implementation review
        ↓
freeze/tag the reusable workflow baseline
        ↓
begin Workflow Manager / Bootstrapper
        ↓
build Controller
        ↓
only then consider richer local-vs-external implementation-review states
and controller-oriented lifecycle redesign
```

The purpose of this boundary is to avoid turning every newly discovered improvement into another prerequisite for the roadmap while still preventing known protocol/tooling inconsistencies from being baked into the reusable distributed baseline.
