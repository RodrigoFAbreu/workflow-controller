---
description: Record the plan or technical approval, or accept the milestone, of the active (or named) work item from the gate policy, when the gate is automatic and every requirement is met. Not user-only -- an automated validation the Workflow performs.
argument-hint: <plan|implementation|acceptance> [work-item-id]
state_writer: true
review-subject: bundle
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Satisfy a gate by policy (`workflow-2.8.0`, `gate-policy-and-reopening`,
`D-GP-Satisfy`; the policy is `docs/ai-workflow/GATE_POLICY.json`, see
`docs/ai-workflow/GATE_POLICY.md`). This command is the action behind the
protocol's `validation` disposition: an automated validation the Workflow
performs, run by a worker like any `automatic` action. It is **not**
`user_only`, because it records no person's decision. It records the Workflow's
own, with the evidence it read.

`/approve-review` stays the human path for every gate in either mode and is
unchanged (INV-5): turning a gate human (`"human_approval": true`, or the
gate's own `human`) removes this command's authority over it at once.

`<bundle_dir>`/`<feedback_dir>` resolve exactly as in `/approve-review`
(`resolve_bundle_dir(repo_root, work_item_id, stage="plan")` for `plan`,
`resolve_bundle_dir(repo_root, work_item_id)` for `implementation`;
`resolve_feedback_dir`).

This command cites `/approve-review`'s steps by number and states only what
differs, so there is one transaction (`OD-W2-11`). Read `approve-review.md`
beside it.

## Arguments

`$ARGUMENTS` is `<plan|implementation|acceptance> [work-item-id]`; the id
defaults to `active_work_item_id`.

- `plan` and `implementation` are specified below.
- `acceptance` is specified in "The acceptance stage" below. It records no
  approval: it accepts the milestone from evidence.

## What is replaced, and what is not

**Replaced (the points `D-GP-Satisfy` names):**

1. **There is no user-only guard and no `user_confirmation` text.** Do not ask
   for one and never write one. `/approve-review`'s "User-only guard,
   mechanism (2)" paragraph and the `validate_user_confirmation` call inside
   its step 3 do not apply. The record's `user_confirmation` is the literal
   `policy:<policy_digest>` (`workflow_state.validate_policy_satisfied_confirmation`),
   never text that claims a person.
2. **Step 3 (the basis) is `workflow_state.resolve_policy_approval_basis`**, not
   `resolve_approval_basis` (which cannot be reused: it demands the confirmation
   text and can return `USER_OVERRIDE`). It keeps the durable-`BLOCK`-pin and
   `BLOCK` refusals, requires a `satisfiable` evaluation, and returns
   `POLICY_SATISFIED`. It never returns `USER_OVERRIDE`: a verdict that is not an
   `APPROVE` naming the current bundle makes the evaluation unsatisfiable.
3. **Step 4 (the record) is `workflow_state.build_policy_approval_record(
   repo_root, state, work_item_id, stage, now=<now>)`**, which evaluates the
   gate (`workflow_gate_policy.evaluate_gate`), resolves the basis, recomputes
   the `review_content_id` and its manifest from the content the reviewers saw
   (the same source `/approve-review` step 4 names; `reviewed_content_commit`
   is unset for `plan`, and is the current `reviewed_implementation_head` for
   `implementation`) and returns the validated `POLICY_SATISFIED` record with its
   `policy_evidence`. It raises `GateNotSatisfiableError`, naming each unmet
   requirement, when the gate is human, a requirement is unmet or the wrapper is
   unreachable; nothing is written then.

**Unchanged, run exactly as `/approve-review` states them, with the same
refusals:**

- Step 0 (the dual-mode version branch, which refuses an unknown governing
  version). The gate is `automatic` only where `D-GP-Gates`' table allows: a
  `"1"` item's plan and technical gates are always human, and a `"2.1"` item's
  technical gate is always human. `evaluate_gate` reports `human` there and
  this command refuses.
- Step 1 (the gate check is `plan_approval_gate_status` /
  `technical_approval_gate_status`, one call each, with the implementation
  stage's durable `BLOCK` pin recorded first), step 2 (recompute fresh, display,
  the generation check and the first `assert_bundle_not_rejected`; at the plan
  stage of a `"2.1"`/`"2.2"` item the bundle-bound check).

## The policy evaluation, then the stage's transaction

1. **Evaluate and report.** Call `workflow_gate_policy.evaluate_gate(repo_root,
   state, work_item_id, "plan_approval")` (or `"technical_approval"` for
   `implementation`) and show the user the mode, the source and each
   requirement with its `detail`. If the gate is `human`, or not `satisfiable`,
   **stop**: report the unmet requirements and the remedies below. Write
   nothing, commit nothing, and record no floor (`LPR-R18-001`: a refused run
   never moves `HEAD` past an open bundle).
2. **Plan stage.** Run `/approve-review plan`'s steps 4a through 6d unchanged
   (`resolve_fresh_plan_approval_members`, the failure-atomicity journal, the
   amendment reservation, staging, the state pin, the staged-set assertion and
   closure proof, the commit, the outcome classification, the verification,
   the working-tree writeback and the journal close) except that:
   - the `record` step 4c builds and forwards to `open_plan_approval_journal` is
     the `build_policy_approval_record(...)` record, and its `user_confirmation`
     argument is that record's `policy:<digest>` literal;
   - **the re-evaluation inside the transaction**: the policy file is an
     uncommitted working-tree input that the state compare-and-swap of step 6.2
     cannot see, so inside step 6.2's guarded window, **before** the
     compare-and-swap and the state pin, and again immediately before step
     6.4's commit, call `workflow_state.assert_policy_still_satisfied(
     repo_root, <the freshly read state>, work_item_id, "plan", record)`. If the
     gate is no longer `satisfiable` (for example a human toggle was turned on
     meanwhile) or the effective policy is no longer the one the record names,
     it raises `GateNotSatisfiableError`: run step 6b's rollback and write
     nothing. The record is never built from an evaluation taken before the
     state was re-read;
   - the plan-approval commit (step 6.4) carries the same two trailers,
     `Workflow-Plan-Approval: <full review_content_id>` and
     `Workflow-Work-Item: <id>`, **and** `Workflow-Gate-Satisfied-By:
     policy:<first 12 characters of the policy digest>`
     (`workflow_state.gate_satisfied_by_trailer(record)`), all three as the
     message's final paragraph (`OPUS-R129-001`);
   - the plan-approval transaction stays unscoped (the whole-file pin of
     `/approve-review plan`); `verify_plan_approval_commit` admits the
     `POLICY_SATISFIED` record and the extra trailer.
3. **Implementation stage.** Run `/approve-review implementation`'s steps 1 to 4
   and the implementation-stage branch of step 6 (steps 4a to 6d are plan stage
   only): `workflow_state.state_transaction(repo_root, lambda state:
   workflow_state.apply_technical_approval(state, work_item_id,
   workflow_state.build_policy_approval_record(repo_root, state, work_item_id,
   "implementation", now=<now>), <now>))`, so the evaluation and the record come
   from the freshly re-read state and a gate turned human meanwhile writes
   nothing. Then one metadata-only commit, staging the state file with
   `workflow_state.stage_scoped_state(repo_root, <work_item_id>)` as
   `/approve-review` does, with `Workflow-Technical-Approval: <full
   review_content_id>`, `Workflow-Work-Item: <id>` and
   `Workflow-Gate-Satisfied-By: policy:<first 12 characters of the digest>` as the
   final paragraph. Immediately after, run both post-commit checks of
   `/approve-review`'s ledger row `I21`:
   `workflow_state.validate_technical_approval_commit(...)` (which admits a
   record that carries `policy_evidence` and `POLICY_SATISFIED`) and
   `workflow_state.verify_post_approval_manifest_match(...)`.
4. **Step 7 (the report), restated for the automatic path** (`LPR-R3-005`):
   report that the approval was recorded **by policy**, not by a person:
   the basis `POLICY_SATISFIED`, the policy digest and source, each requirement
   and its detail, both ledger stages' verdict hashes, bundle ids and run
   references, the `trust` statement (`review_verdicts: orchestrator`: the
   Workflow guarantees binding, freshness and audit of the verdicts it was
   given, not that a review happened), and the resulting phase. State that
   `/approve-review` remains available, and that turning human approval on is
   the stronger mode.
5. **Record the floor, after the satisfying commit** (`LPR-R18-001`,
   `LPR-R19-O3`): if the evaluation that committed observed a setting stricter
   than `gate_policy_floor`, record it in its own floor commit
   (`workflow_state.commit_gate_policy_floor`, trailer
   `Workflow-Gate-Policy-Floor`). If the floor commit or its validator refuses
   **after** the satisfying commit, the approval stands and is reported as done,
   the floor stays virtual (every later evaluation recomputes it), and the report
   names the failed floor commit and its cause.

## The acceptance stage

`/satisfy-gate acceptance [work-item-id]` accepts the milestone from evidence
(`D-GP-Acceptance`). It is the policy's own act, not a person's decision, so
it carries no `user_confirmation` and `/accept-milestone`'s step 1 (the
user-only guard) does not apply. `/accept-milestone` stays the human path in
either mode and is unchanged apart from its added `requires_pr_approved`
precondition (INV-5). Read `accept-milestone.md` beside this section: it runs
that command's steps 0, 2, 2a, 2b and 3 to 8 **unchanged, cited by number**,
and this stage states only what differs.

1. **Open with the Workflow's own query.** The step every acceptance opens
   with: `workflow_state.satisfy_acceptance_gate(repo_root, work_item_id,
   now=<now>)` runs, inside its one `state_transaction`, the fixed GitHub query
   (`workflow_forge.query_forge_pr_facts`, `D-GP-Trust`, over the `gh` that
   `resolve_gh` resolved, `D-GP-ThreatModel`) for the anchor commit and stores the
   result as `gate_evidence.pr` (provenance `workflow_gh`). A failed query
   (`forge_unavailable`: `gh` missing, unauthenticated, failed or timed out;
   `forge_undecidable`: an unsafe `gh`, two open pull requests or a full page)
   **refuses**: the gate does not pass and nothing is written, whatever
   `orchestrator_forge` facts are stored, including a self-consistent all-green
   one. Report the code and its remedy (install or authenticate `gh`, or turn
   acceptance human). A reported fact is tighten-only (`LPR-R9-002`): with `gh`
   available it changes nothing, because this query decides.
2. **Evaluate, inside the same transaction.**
   `workflow_gate_policy.evaluate_gate(repo_root, state, work_item_id,
   "acceptance")` on the state the query just stored: `checkpoints_complete`,
   `technical_approval_current`, `functional_flows_passed`, `pr_fact_current`,
   `no_standing_pr_objection`, `ci_green` (when `require_ci`) and `pr_approved`
   (when `requires_pr_approved`), each recomputed now. If the gate is `human` or
   not `satisfiable`, **stop**: show the mode and each unmet requirement with its
   `detail`, store only the pull-request fact the query read, write nothing else,
   commit nothing and record no floor (`LPR-R18-001`). The result's `obtainable`
   names the evidence a reporter can still supply (`functional_evidence`,
   `pr_review_result`); a failed flow, failed checks, a standing
   `CHANGES_REQUESTED`, a stale technical approval or a pull request for
   different content is not obtainable: fix it through `/apply-functional-review`
   (or `/apply-pr-review`), or turn acceptance human. A fact for an **older**
   head (a bounded fix approved locally but not pushed) blocks `pr_fact_current`:
   push the approved head and re-run `/satisfy-gate acceptance`.
3. **Complete, with the record.** In the same mutator,
   `workflow_state.apply_acceptance_satisfaction` calls
   `workflow_state.complete_work_item` -- so every refusal of the human path
   still holds (an incomplete child, an outstanding checkpoint, an unsatisfied
   completion obligation; `/accept-milestone` step 2a) and leaves the state
   byte-identical -- and sets the item's `acceptance_satisfaction`:
   `policy_digest`, `policy_source`, the file, adopted and floor digests, every
   requirement with its `detail`, the inputs read (anchor commit and identity,
   technical approval, each flow's `log_digest` and `run_ref`, the pull-request
   fact's id, head, provenance `workflow_gh` and `raw_sha256`), the `trust`
   statement, `evaluated_at` and `workflow_release`. A re-acceptance overwrites it.
4. **The remaining steps.** Run `/accept-milestone` steps 2b and 3 to 8 as
   written (the roadmap, `docs/ACTIVE_MILESTONE.md`, the archive copy of step 5,
   the completion commit, the next action). The completion commit's final
   paragraph carries `Workflow-Work-Item: <id>` **and**
   `Workflow-Gate-Satisfied-By: policy:<first 12 characters of the policy digest>`
   (`workflow_state.acceptance_satisfied_by_trailer(record)`), after any
   `Co-Authored-By:` lines (`OPUS-R129-001`).
5. **Record the floor, after the satisfying commit**, exactly as step 5 of "The
   policy evaluation, then the stage's transaction" says (`LPR-R23-003`:
   evaluate against the virtual floor, perform the satisfying commit, and only
   afterwards record a stricter observed setting in its own floor commit).
6. **Report** that the milestone was accepted **by policy**, not by a person:
   the policy digest and source, each requirement and its detail, the functional
   evidence's `log_digest`, `reporter` and `run_ref`, the pull-request fact's
   provenance and `raw_sha256`, the `trust` statement (review verdicts and
   functional evidence are trusted from the orchestrator; the pull-request fact
   comes from the Workflow's own query), and that turning acceptance human is the
   stronger mode.

A remediation child (`<parent-id>-remediation-<n>`) is accepted the same way,
by naming its id; its parent's own acceptance then unblocks.

## Unmet requirements and their remedies

`evaluate_gate` names each unmet requirement:

- `gate_reachable`: the wrapper's `cause` (`bundle_generation_mismatch`,
  `no_review_round`, `review_blocked`, `protected_path_dirty`,
  `implementation_provenance_stale`, `review_ledger_stale`, and so on), with the
  same remedies `/approve-review` step 1 gives.
- `verdict_approve_bound`: the latest verdict is not an `APPROVE` naming the
  current bundle. A wrapper-only regeneration after the review changes the
  bundle id; only the human path (`/approve-review`, which can record an
  override) can approve it.
- `distinct_reviewer_models`: the two stages must declare different
  `Reviewer model:` families. At the open manual-review phase the remedies are
  D-GP-Ingest's: re-submit with a second family, or turn that gate human. After
  the stage is closed no command can re-record a stage: turn that gate human
  (immediate; `/approve-review` then proceeds) or withdraw (`/milestone-plan
  <id>` at the plan stage; the implementation stage's own bundle withdrawal and
  regeneration, then both review stages again).
- `review_evidence_audited`: a stage was recorded while its gate was human, so
  it carries no `verdict_sha256`. Turn that gate human (immediate) or withdraw
  and re-review.

Never edit the plan, registry, mapping, command, product or bundle-content
files, and never another work item's fields. Never invoke this command for a
gate the policy makes human: that is `/approve-review`'s, and a person's.
