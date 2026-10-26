# Workflow Orchestration Protocol v1

The normative specification of Orchestration Protocol v1, version `1.5`
(`1.0` first shipped in Workflow 2.7.0; `1.1` adds the gate-policy rows and
actions in Workflow 2.8.0; `1.2` adds the user-only `legacy.retire`
alternative of row 3 and `implementation.resume` alternative of row 38c in
Workflow 2.9.0; `1.3` adds row `1b` and the advisory `default_workflow_version`
check in Workflow 2.10.0; `1.4` adds the `review_round` snapshot field in
Workflow 2.12.0; `1.5` adds the four per-role `review_round` counts in
Workflow 2.15.0). An orchestrator (for example the Workflow
Controller) drives a repository's Workflow through this protocol only. It
copies no Workflow phases, artifact paths, helper names or transition
rules: the Workflow owns what the lifecycle means, and the orchestrator owns
how it is run.

- **Implementation:** `scripts/workflow_protocol.py` (stdlib-only).
- **Schema:** `docs/ai-workflow/orchestration-protocol-v1.schema.json`
  (JSON Schema 2020-12), which defines the envelope and every operation's
  `result` under `$defs.results.properties.<operation>` (cited below as
  `$defs.results.<operation>`).
- **Tests:** `scripts/workflow_protocol_test.py`. It checks every response
  against the schema, and it parses the tables of this document marked as
  mirrored below and requires them to equal the code's own tables, row for
  row and in order.

The protocol reports the gates of `MILESTONE_WORKFLOW.md` and adds none.
Every existing command still works as before when a person runs it.

## 1. Versioning

- The protocol version is `MAJOR.MINOR`, starting at `1.0`. It is
  independent of the Workflow release (2.8.0) and of any work item's
  governing version (`1`, `2.1`, `2.2`).
- A **minor** bump only adds things: optional response fields, new action
  ids, new error codes, new artifact or result kinds, or new capabilities.
- A **major** bump is anything else.
- `--protocol-major N` asserts the consumer's major. Any integer `N` other
  than `1` refuses with `unsupported_protocol` before any other work,
  including before `--repo-root` is checked (a malformed global option,
  such as a non-integer `N`, is `invalid_request`). Omitting it is allowed,
  for people.
- `describe` reports the protocol version, the supported majors and the
  supported governing versions. The Workflow releases tested against v1 are
  listed only here, never in a response: **Workflow 2.7.0** (protocol `1.0`), **Workflow 2.8.0** (protocol `1.1`), **Workflow 2.9.0**, **Workflow 2.9.1** and **Workflow 2.9.2** (all protocol `1.2`), **Workflow 2.10.0** and **Workflow 2.11.0** (both protocol `1.3`), **Workflow 2.12.0**, **Workflow 2.13.0** and **Workflow 2.14.0** (all protocol `1.4`), **Workflow 2.15.0**, **Workflow 2.16.0**, **Workflow 2.17.0**, **Workflow 2.18.0**, **Workflow 2.18.1**, **Workflow 2.18.2**, **Workflow 2.18.3**, **Workflow 2.18.4**, **Workflow 2.18.5**, **Workflow 2.18.6**, **Workflow 2.18.7**, **Workflow 2.19.0**, **Workflow 2.19.1**, **Workflow 2.19.2** and **Workflow 2.20.0** (all protocol `1.5`).

## 2. Invocation and the envelope

```text
python3 scripts/workflow_protocol.py [--repo-root PATH] [--protocol-major N] <operation> [operation args]
```

The operations are `describe`, `verify`, `resolve-artifact`, `next-action`,
`reconcile` and `record-external-result`. The script imports
`workflow_state` and `workflow_fingerprint` from its own directory and
never shells out to them. An orchestrator runs it as a subprocess and never
imports it. `--repo-root` defaults to the current directory.

stdout is exactly one JSON document, the **envelope**
(`$defs.envelope`); diagnostics go to stderr. There is no `--help`.

```json
{
  "protocol": {"name": "workflow-orchestration", "version": "1.5"},
  "workflow_release": "2.20.0",
  "operation": "next-action",
  "ok": true,
  "result": { "...": "operation-specific" }
}
```

On refusal, `"ok": false` and the envelope carries `"error"` instead of
`"result"` (`$defs.error`):

```json
{"code": "stale_decision", "message": "…", "retryable": true,
 "native": {"exception": "PlanReviewNotPublishedError", "message": "…"}}
```

`native` is `null` when no Workflow exception is involved. `operation` is
`null` when the operation could not be determined from the arguments.
`workflow_release` is the module constant `WORKFLOW_RELEASE`, the release
the script's bytes belong to; it never reads `workflow-manager`'s
installation record.

| exit code | meaning |
| --- | --- |
| `0` | `ok: true` |
| `3` | a refusal with a stable code |
| `2` | `invalid_request`, which covers argument errors |
| `1` | `internal_error`, an unexpected exception |

Every exit code still prints an envelope.

## 3. Error codes

| code | meaning | retryable |
| --- | --- | --- |
| `unsupported_protocol` | unknown protocol major | no |
| `invalid_request` | bad arguments, or a malformed input document | no |
| `unknown_work_item` | the id is not a key of `work_items` | no |
| `no_work_item` | no id was given and no `active_work_item_id` exists, for operations that need one; reserved, since every 1.0 operation that needs an item requires `--work-item` | no |
| `state_unreadable` | the state or config file is missing or corrupt, or the lock file cannot be opened | no |
| `state_invalid` | `validate_state` refused | no |
| `stale_decision` | the decision basis no longer matches the state | yes, by re-deciding |
| `not_applicable` | the operation does not apply at the item's phase or governing version | no |
| `unsupported_result_kind` | a result kind is reserved for a later protocol version | no |
| `refused` | a Workflow precondition refused; `native` names the exception | no |
| `internal_error` | an unexpected exception | no |

**The Workflow exception domain.** A **Workflow exception** is an instance
of a class `C` such that `issubclass(C, Exception)` and `C.__module__` is
the `__name__` of the `workflow_state` or `workflow_fingerprint` module
object the protocol imported. Then:

- a Workflow exception whose class the protocol maps explicitly has that
  code: `InvalidWorkItemIdError` is `invalid_request`,
  `FeedbackLayoutUndecidableError` is `state_unreadable`, and
  `UnknownFeedbackLayoutError` is `state_invalid`;
- any other Workflow exception, including a class added by a later
  release, is `refused`, never `internal_error`;
- an exception that is not a Workflow exception is `internal_error`, even
  when Workflow code raised it (a built-in `KeyError` is a defect, not a
  refusal). An `OSError` is `internal_error` too, except from the
  protocol's own read of the state, the config and the lock file, which is
  `state_unreadable`; an unreadable operation input file (`--decision`,
  `--input`), which is `invalid_request`; and `verify`'s read of the
  installation record, which fails its check.

`state_unreadable` is never retryable: the reads take a shared lock that
blocks until it is granted, so there is no timeout to retry.

## 4. State identity and the decision basis

The **state identity** of a work item is

```text
sha256(canonical_json({"schema_version": <state schema_version>,
                       "work_item_id": <id>,
                       "work_item": work_items[<id>]}))
```

where `canonical_json` is the Workflow's canonical serialization
(`workflow_state._canonical_json_bytes`). It covers one work item, not the
whole file: unrelated concurrent work items never stale each other's
decisions, and `active_work_item_id` is not part of it.

Every response about a work item carries a `basis` (`$defs.basis`):

```json
{"work_item_id": "…", "state_revision": 7, "state_identity": "<64 hex>",
 "phase": "IMPLEMENTING", "head": "<40 hex>",
 "checkpoints": {"CP1": "COMPLETE", "CP2": "IN_PROGRESS"}}
```

Staleness is decided by `state_identity` alone; `head` and `checkpoints`
are informational. `next-action --expect-state-identity <hex>` refuses with
`stale_decision` when the current identity differs, and a consumer calls it
immediately before launching an action.

The identity covers the state only. Some catalogue rows also read inputs
outside it: the feedback file, the `REJECTED` marker, the worktree (the
publication status, the generation check, dirty protected paths) and
`FUNCTIONAL_REVIEW.md`. A change to those inputs after a decision is not a
`stale_decision`. It is still caught, because every command re-checks its
own preconditions when it runs, and `reconcile` re-decides from the new
inputs.

## 5. Operations

Every read-only operation reads the state under a shared lock on
`.ai-review/runtime/WORKFLOW_STATE.lock` when that file exists, opened
read-only and never created (when it is absent no writer has run, and the
read proceeds unlocked), and writes nothing. `record-external-result` is the only
operation that writes.

### 5.1 `describe`

`describe` reads nothing from the repository except to confirm that the
directory exists. Its result (`$defs.results.describe`) is
`workflow_release`, `protocol_version`, `supported_protocol_majors`
(`[1]`), `supported_governing_versions` (`["1", "2.1", "2.2"]`) and
`capabilities`: `operations`, `dispositions`, `action_ids`,
`artifact_kinds`, `external_result_kinds`, `reserved_result_kinds` and
`error_codes`. Every list is sorted and is the module's own table.
`supported_governing_versions` is the protocol's, never a repository's
`WORKFLOW_CONFIG.json`.

### 5.2 `verify`

`verify` is read-only. Its result (`$defs.results.verify`) is
`{healthy, checks: [{id, status, detail}]}`, with `status` one of `pass`,
`warn`, `fail` or `skip`, and these checks in order:

1. `state_readable`: the state file and the config parse.
2. `state_valid`: `validate_state(state, repo_root=…)` passes, including the
   registry-to-mirror `plan_revision` check, and (protocol `1.5`)
   `validate_review_round_counts(state)` reports no shape problem in any work
   item's `review_round_counts`. `validate_state`, and so every operational
   loader (`next-action`, `record-external-result`, the writers), does not apply
   the counter check: a malformed counter is reported here and refuses nothing
   else.
3. `config_valid`: `validate_config` passes, or no config file exists and
   the pre-activation default applies.
4. `active_item_resolvable`: `active_work_item_id` is null or names a
   non-terminal item.
5. `checkpoint_completions_provable`: for each non-terminal item with
   `COMPLETE` checkpoints, `prove_checkpoint_completions` proves them by
   reachable trailer commits. That is `verify_checkpoint_completions`'
   discovery: the checkpoint's `Workflow-Checkpoint` trailer commits in
   `base..HEAD`; a single one resolves; otherwise a single first-parent
   ancestor of `HEAD` resolves; otherwise exactly one first-parent candidate
   must pass the tie-break predicate. Here that predicate is that the state
   committed there records the checkpoint `COMPLETE` and the commit strictly
   descends from the checkpoint's recorded `start_commit`. Whichever path
   resolved the commit, including a sole trailer commit, the proof then
   requires every `COMPLETE` checkpoint's resolved commit to strictly
   descend from its recorded `start_commit`; a reopened checkpoint whose
   only completion commit is its earlier one fails. A checkpoint re-completed after a plan amendment has two
   such commits, which `verify_checkpoint_completions` alone refuses as
   ambiguous; the descent filter leaves its latest completion, so the proof
   passes.
6. `installation_release_matches`: when `.workflow-manager/installation.json`
   exists, its `workflow_version` equals `WORKFLOW_RELEASE`; `skip` when it
   is absent.
7. `protocol_ready`: checks 1 to 4 passed.
8. `gate_policy` (workflow-2.8.0, advisory): `pass` when no
   `docs/ai-workflow/GATE_POLICY.json` is present (the default applies) or the
   file equals the adopted policy; `warn` for an unadopted differing file, an
   ignored loosening, a floor not yet recorded in a commit, a floor holding a
   setting the file no longer carries, and a gate-lowering adoption; `fail`
   for an invalid file and for a failed provenance of the adopted policy or
   the floor. It never changes `healthy` and is not part of check 7.
9. `default_workflow_version` (workflow-2.10.0, protocol `1.3`, advisory):
   `warn` when the config's `default_workflow_version` is one new work items
   no longer start on (`NEW_WORK_RETIRED_VERSIONS`, today `2.1`); its detail
   names the default, that new work items are refused, and the next step
   (`RETIRED_DEFAULT_NEXT_STEP`); `pass` otherwise; `skip` when the
   configuration is unreadable (check 3 did not pass). It never changes
   `healthy` and is not part of check 7: an existing `2.1` item is still
   driven normally.

A check fails on a Workflow or Git refusal. After an unreadable state,
checks 2 to 5 are `skip`, check 6 still runs, and check 7 fails. A failing
check gives `healthy: false` with `ok: true`: `verify` refuses only with
`unsupported_protocol` or `invalid_request` (or `internal_error` for a
defect).
`verify` does not check installation digests; that is `workflow-manager
verify`'s job.

### 5.3 `resolve-artifact`

```text
resolve-artifact --work-item ID --kind KIND
```

The result (`$defs.results.resolve-artifact`) is
`{kind, path, exists, basis}`, `path` repository-relative (`null` when the
item declares none). Each kind is resolved by the Workflow's own helper:

| kind | resolved by |
| --- | --- |
| `review_feedback` | `resolve_feedback_dir` + `REVIEW_FEEDBACK.md` |
| `functional_review` | `resolve_feedback_dir` + `FUNCTIONAL_REVIEW.md` |
| `review_bundle` | `resolve_bundle_dir`, with `stage="plan"` at a plan-stage phase |
| `plan_review_inputs` | `resolve_plan_review_inputs_dir` |
| `plan_document` | the item's `plan_path` |
| `functional_checklist` | the functional-review checklist path |

An unknown kind is `invalid_request`. This is an escape hatch for display
and hand-off ("upload this bundle"): no catalogue decision requires the
orchestrator to read a resolved artifact.

### 5.4 `next-action`

```text
next-action [--work-item ID] [--expect-state-identity HEX]
```

`next-action` evaluates the action catalogue (section 6) for the named
work item, or the active one. Its result (`$defs.results.next-action`) is
a **decision**:

```json
{"basis": { "...": "section 4" },
 "snapshot": {"phase": "…", "governing_workflow_version": "…", "work_item_type": "…",
              "plan_revision": 2, "implementation_revision": null,
              "current_checkpoint_id": null, "next_checkpoint_id": "CP2",
              "registry_complete": false, "plan_approval": "CURRENT",
              "technical_approval": null, "plan_review_publication_status": null,
              "review_round": {"plan": 2, "implementation": null, "current": null,
                              "plan_local": 2, "plan_external": 2,
                              "implementation_local": 0,
                              "implementation_external": 0}},
 "row": "23",
 "disposition": "automatic",
 "action": {"id": "implementation.checkpoint",
            "arguments": {"work_item_id": "…", "checkpoint_id": "CP2"},
            "invocation": "/milestone-implement …",
            "worker": {"role": "implementer", "fresh_session": false,
                       "independent_of": [], "user_only": false},
            "allowed_results": ["progress", "no_progress"]},
 "satisfied_by": null,
 "alternatives": [],
 "reason": {"code": "…", "text": "…", "remedy": null}}
```

- `row` is the catalogue row that matched. Row ids are stable labels; a
  row added later gets a letter suffix (`11a`).
- `disposition` is one of `automatic`, `validation`, `human_gate`,
  `external_gate`, `blocked` or `complete`.
- `action` is `null` for `complete` and for every `blocked` decision; a
  route out of a block appears only in `alternatives`. `action.id` is the identity a consumer dispatches on;
  `invocation` is the rendered command, for display. `arguments` always
  carries `work_item_id` for an item's decision, plus row-specific
  arguments (`checkpoint_id` at row 23). `allowed_results` is copied from
  the legal-edge table (section 7), empty for a non-automatic action.
- `satisfied_by` names the `record-external-result` kind that resolves an
  `external_gate`.
- `alternatives` lists other legal actions, each rendered like `action`
  (and carrying its own `satisfied_by` where it has one). An orchestrator
  never runs an alternative on its own; alternatives are for the person who
  resolves a gate or a block.
- `reason` is `{code, text, remedy}`, plus `native` for
  `condition_refused`. `remedy` is `null` when the row has none.
- `snapshot` is the item's state at decision time, plus the values the
  evaluation computed (`next_checkpoint_id`, `registry_complete`,
  `plan_review_publication_status`, each `null` when not computed).
- `review_round` (protocol `1.4`, an item's decision only) is derived on every
  call from state already persisted and never stored:
  - `plan` is the number of plan-review rounds, an exact count or `null`,
    "not derivable". It is the number of distinct contents taken out of
    review (`consumed_plan_review_content_ids` and the `plan_review_binding`
    slot's consumed id) plus one while the binding record is `PUBLISHED` or
    `BOUND`, approved items included, so it does not drop at approval. It is
    `null` before the first publication and for every shape whose history the
    Workflow cannot show complete: the fail-closed legacy marker, a
    `2.6.0`-shaped item (a consumed slot and no history list), a slot id the
    list lacks, and a history shorter than the consumed slot's
    `plan_revision`. A number never becomes a smaller number, but it can
    return to `null` (a revision advanced without a consumed round); a
    consumer that sees `null` counts the rounds itself, retroactively.
  - The value is exact for an item whose plan-review rounds all ran under
    Workflow 2.7.0 or later. An item begun before 2.7.0, or in a repository
    ever moved back below 2.7.0, can read a number lower than its true round,
    undetectably: for cap purposes the value is advisory for such items, and
    a consumer that must enforce a hard cap counts rounds itself.
  - `plan` is not the ledgers' per-stage `round`. A ledger's `round` is the
    number the reviewer wrote in the verdict header, not a Workflow count; for
    the number of verdicts a role has recorded, read the four per-role fields
    below.
  - `implementation` is the item's `implementation_revision` (`null` until
    set). A same-content republication after a fix is a new review under the
    same number.
  - `current` is the review stage the phase waits for, from the phase and
    governing version alone: `plan_local`, `plan_external`,
    `implementation_local`, `implementation_external`, else `null`. A `2.2`
    item at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` (both stages approved,
    ready for approval) reads `null`.
  - `plan_local`, `plan_external`, `implementation_local` and
    `implementation_external` (protocol `1.5`) are the number of verdicts the
    local stage and the external stage have recorded at that review stage: one
    round of a role is one `APPROVE` or `REVISE` recorded by that role's writer
    (a `BLOCK`, a person's approval, a `USER_OVERRIDE`, a gate-policy
    satisfaction and a refused ingest record no round). Each is read from the
    work item's persisted `review_round_counts`, which the writer increments in
    the same state transaction as the verdict, so the number and the verdict
    cannot be separated. Each is an integer, or `null` when the Workflow holds
    no count: an item created before 2.15.0 (no key), a promoted legacy item, a
    feedback-only item (its `EXTERNAL_*` phases record no stage verdict), a
    stage that records no per-role rounds, or a malformed key. A number never
    resets: a fix round, a post-fix republication or a plan amendment adds to
    it. The external counter is the second stage whichever harness produced the
    verdict. These count verdicts, `plan` counts contents taken out of review
    plus one in review, so `plan_local + plan_external` can be larger or
    smaller than `plan`; do not compare them.

With no work item named and none active, the result has no `basis`; its
`snapshot` is `{"work_item_ids": [...]}`, the sorted keys of `work_items`,
and the catalogue's no-item rows (1, 1b and 1a) decide.
`--expect-state-identity` then refuses with `invalid_request`.

Refusals: `stale_decision` (section 4); `unknown_work_item`;
`not_applicable` for a governing version outside
`supported_governing_versions`; `state_invalid` for an unknown phase or a
state `validate_state` refuses.

### 5.5 `reconcile`

```text
reconcile --decision FILE [--work-item ID]
```

`FILE` holds the exact `next-action` result whose `action` the
orchestrator executed, or the whole `next-action` envelope. Passing it
back verbatim keeps the Workflow free of decision history. `reconcile`
re-reads the state, classifies what the action did (section 7) and writes
nothing. Its result (`$defs.results.reconcile`):

```json
{"class": "progress",
 "from": {"phase": "…", "state_identity": "…"},
 "to": {"phase": "…", "state_identity": "…"},
 "evidence": {"completed_checkpoints": ["CP2"], "started_checkpoints": [],
              "recorded_stage": null},
 "invalid_reasons": [],
 "basis": { "...": "section 4" },
 "next": { "...": "the next-action decision for the new state" }}
```

`reconcile` accepts an `automatic` decision only. A gate is resolved
outside the orchestrator's run, by a person or by `record-external-result`
(section 8.2 states when a courier may carry a verdict),
and the orchestrator then calls `next-action` again. Any other decision,
and any decision that is not a well-formed `next-action` result (an action
whose rendering differs from the catalogue's, a row that cannot emit it,
a basis or snapshot that does not agree), refuses with `invalid_request`.
So does `--work-item` naming another item than the decision's (it is
ignored for a `plan.start` decision). A decision's `row` may be omitted;
when present, it must be a row that emits the action.

### 5.6 `record-external-result`

```text
record-external-result --work-item ID --kind KIND --input FILE [--run-ref REF]
```

`FILE` holds the external reviewer's verdict text, verbatim, for the two
verdict kinds. The kinds are `plan_review_verdict`,
`implementation_review_verdict`, and (protocol `1.1`) `functional_evidence`
and `pr_review_result`, whose `FILE` is a JSON object (the input shapes are
`$defs.inputs.functional_evidence` and `$defs.inputs.pr_review_result` of the
schema); both are accepted under every gate policy, and a refusal of either
(a malformed record, an unknown head, a missing or inconsistent `forge`
provenance block) is `refused` with the library's reason code in its message
and nothing stored. No kind is reserved in `1.1`; any other kind, or an
unreadable input, is `invalid_request`. For the two verdict kinds the
operation calls
`workflow_state.ingest_manual_review_verdict`, the same ingest the
`/record-manual-plan-review` and `/record-manual-implementation-review`
commands call. It selects one row of this table from the item's phase and
governing version:

| kind | gv | accepted phase | required header fields | guards, in order | records |
| --- | --- | --- | --- | --- | --- |
| `plan_review_verdict` | `2.1`, `2.2` | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `Status:`, `Reviewer role:`, `Reviewed review_content_id:` | `assert_manual_feedback_names_work_item`; `assert_local_generation_matches`; `assert_bundle_not_rejected`; `assert_plan_review_bundle_bound`; `validate_manual_plan_review_preconditions`; `check_manual_stage_bundle_id_advisory`; `assert_bundle_not_rejected` again, immediately before the first write | `record_manual_plan_review` |
| `implementation_review_verdict` | `2.2` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | the same three | `assert_manual_feedback_names_work_item`; `verify_implementation_review_bundle`; `assert_local_generation_matches`; `assert_bundle_not_rejected`; `validate_manual_implementation_review_preconditions`; `check_manual_stage_bundle_id_advisory`; `assert_bundle_not_rejected` again, immediately before the first write | `record_manual_implementation_review` |
| `plan_review_verdict` | `1` | `AWAITING_EXTERNAL_PLAN_REVIEW` | `Status:` and the three binding fields | `assert_manual_feedback_names_work_item`; `assert_bundle_not_rejected`; `assert_feedback_matches_bundle` against the current plan bundle | nothing (feedback only) |
| `implementation_review_verdict` | `1`, `2.1` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `Status:` and the three binding fields | `assert_manual_feedback_names_work_item`; `assert_bundle_not_rejected`; `assert_feedback_matches_bundle` against the current implementation bundle | nothing (feedback only) |

The three binding fields are `Reviewed bundle ID:`, `Reviewed base commit:`
and `Work item:`. The guards, the feedback write and the state publication
are one `state_lock` critical section: the verdict is written to the
resolved `REVIEW_FEEDBACK.md` atomically, then a two-stage row's state is
published. Identical bytes are a no-op. A crash between the two leaves the
phase unchanged with the verdict in the file, and a retry through either
path records it. At a feedback-only row, a different verdict that already
binds to the current bundle refuses (`ConflictingReviewFeedbackError`).

`--run-ref` (workflow-2.8.0, optional) is the reporter's own identifier for
the run that produced the verdict. While the stage's gate is automatic
(`GATE_POLICY.json`), the ledger entry records it beside `verdict_sha256`
(declared, never verified; `null` when absent), and the entry also records the
verdict's `Reviewer model:` header line when the effective policy requires
distinct reviewer models. Under that requirement an `APPROVE` that declares no
model, or the same family as the other stage, is refused
(`DistinctReviewerModelsRequiredError`, a `refused` result) before any write.
Under a human gate nothing changes: no key, no refusal.

`round` is the header-block `Round:` value; a `Round:` that is not a
positive integer refuses (`ManualVerdictHeaderError`), and when it is
absent, `round` is the round of the same stage's local `APPROVE` of the
current content. `bundle_id` is the verdict's `Reviewed bundle ID:`, or
`null` when the reviewer named none, with an advisory that includes
`Reviewed bundle ID: absent`.

The result (`$defs.results.record-external-result`) is
`{stage, verdict, review_content_id, round, bundle_id, advisory, basis}` for
a verdict kind (`round` and `bundle_id` are `null` for a feedback-only row),
`{stage: "functional", flow_id, identity, basis}` for `functional_evidence`
and `{stage: "pr_review", slot, fact_id, basis}` for `pr_review_result`. A refusal that
means "no row accepts this verdict here" (the wrong phase or governing
version, or the stage already recorded for the current content) is
`not_applicable`; every other Workflow refusal is `refused`. A retry after
an ingest that moved the phase meets the wrong phase (`not_applicable`); a
retry of a `BLOCK` or a feedback-only verdict with identical bytes succeeds
as a no-op. The orchestrator never reads or
writes a feedback path itself.

## 6. The action catalogue

### 6.1 Actions and worker roles

Each action id names the command it runs (`.claude/commands/<command>.md`),
its rendered invocation (`{id}` is the work item) and its worker. Mirrored
by `ACTIONS`:

| action id | command | invocation | role | fresh_session | independent_of | user_only |
| --- | --- | --- | --- | --- | --- | --- |
| `acceptance.satisfy` | `satisfy-gate` | `/satisfy-gate acceptance {id}` | `validator` | no | — | no |
| `functional.apply_findings` | `apply-functional-review` | `/apply-functional-review {id}` | `applier` | no | — | no |
| `functional.evidence.external` | — | — | `external` | no | — | no |
| `functional.prepare` | `prepare-functional-review` | `/prepare-functional-review {id}` | `implementer` | no | — | no |
| `functional.review` | — | — | `user` | no | — | no |
| `functional.review.advisory` | `review-functional` | `/review-functional {id}` | `independent_reviewer` | yes | `implementer` | no |
| `implementation.apply_review` | `apply-implementation-review` | `/apply-implementation-review {id}` | `applier` | no | — | no |
| `implementation.approve` | `approve-review` | `/approve-review implementation {id}` | `user` | no | — | yes |
| `implementation.checkpoint` | `milestone-implement` | `/milestone-implement {id}` | `implementer` | no | — | no |
| `implementation.record_external` | `record-manual-implementation-review` | `/record-manual-implementation-review {id}` | `applier` | no | — | no |
| `implementation.recover_provenance` | `recover-implementation-provenance` | `/recover-implementation-provenance {id}` | `implementer` | no | — | no |
| `implementation.resume` | `resume-implementation` | `/resume-implementation {id}` | `user` | no | — | yes |
| `implementation.review.external` | — | — | `external` | no | — | no |
| `implementation.review.local` | `review-implementation` | `/review-implementation {id}` | `independent_reviewer` | yes | `implementer`, `self_reviewer` | no |
| `implementation.satisfy` | `satisfy-gate` | `/satisfy-gate implementation {id}` | `validator` | no | — | no |
| `implementation.self_review` | `milestone-implement` | `/milestone-implement {id}` | `self_reviewer` | no | — | no |
| `legacy.retire` | `retire-legacy-work-item` | `/retire-legacy-work-item {id}` | `user` | no | — | yes |
| `milestone.accept` | `accept-milestone` | `/accept-milestone {id}` | `user` | no | — | yes |
| `plan.apply_review` | `apply-plan-review` | `/apply-plan-review {id}` | `applier` | no | — | no |
| `plan.approve` | `approve-review` | `/approve-review plan {id}` | `user` | no | — | yes |
| `plan.author` | `milestone-plan` | `/milestone-plan {id}` | `planner` | no | — | no |
| `plan.record_external` | `record-manual-plan-review` | `/record-manual-plan-review {id}` | `applier` | no | — | no |
| `plan.review.external` | — | — | `external` | no | — | no |
| `plan.review.local` | `review-plan` | `/review-plan {id}` | `independent_reviewer` | yes | `planner` | no |
| `plan.satisfy` | `satisfy-gate` | `/satisfy-gate plan {id}` | `validator` | no | — | no |
| `plan.start` | `milestone-plan` | `/milestone-plan` | `planner` | no | — | no |
| `plan.withdraw` | `milestone-plan` | `/milestone-plan {id}` | `planner` | no | — | no |
| `pr.apply_review` | `apply-pr-review` | `/apply-pr-review {id}` | `applier` | no | — | no |
| `pr.review.external` | — | — | `external` | no | — | no |
| `review.resolve_block` | — | — | `user` | no | — | no |

The worker roles are `planner`, `implementer`, `self_reviewer`,
`independent_reviewer`, `applier`, `validator` (protocol `1.1`: the
Workflow's own automated validation of a gate by policy, never a person's
decision), `user` and `external`.
`fresh_session` asks for a session that has not seen the work, and
`independent_of` lists the roles whose sessions the worker must not share.
A `user_only` action always has role `user`, its command file carries
`disable-model-invocation: true`, and it is never `automatic`: only a
person runs it. An action with no command (`plan.review.external`,
`implementation.review.external`, `review.resolve_block`,
`functional.review`) is work done outside the repository's commands.
Some user-only commands are not actions at all, because no phase or state
row can see the situation they answer: `/request-plan-amendment`,
`/adopt-gate-policy` and `/release-checkpoint-start`. `next-action` never
names them.

### 6.2 Terms used in the conditions

- **fb** is the item's `<feedback_dir>/REVIEW_FEEDBACK.md`, read with the
  Workflow's verdict parser (`parse_review_feedback_header`). An absent
  file, or one whose `Work item:` names another item, is "no fb".
- **P** is the fresh plan-stage `review_content_id`
  (`plan_review_publication_status`'s `fresh_review_content_id`). **I** is
  the current implementation-stage `review_content_id`, computed the way
  `/approve-review` computes it. **B** is the `bundle_id` recomputed over
  the stage's resolved bundle directory (`resolve_bundle_dir`, with
  `stage="plan"` at plan-stage phases).
- **fb is current** means, by stage and governing version:
  - at a two-stage plan phase (`2.1`, `2.2`): fb's `review_content_id`
    equals **P**; its bundle id is advisory and not compared;
  - at a two-stage implementation phase (`2.2`): fb's `review_content_id`
    equals **I**, for the same reason;
  - at the `1` plan stage and the `1`/`2.1` implementation stage:
    `assert_feedback_matches_bundle` against **B**, the item's
    `base_commit` and its id accepts fb.
- **An unrecorded manual verdict** is a current fb whose `Reviewer role:`
  normalizes to the stage's `MANUAL_EXTERNAL_*_REVIEW`, whose `Status:` is
  `APPROVE` or `REVISE`, and for whose content the ledger records no manual
  stage.
- **The plan gate status** and **the technical gate status** are
  `plan_approval_gate_status` and `technical_approval_gate_status`, the
  read-only wrappers `/approve-review` calls. Each returns `reachable` and,
  when it is false, the first failing `cause`: the generation check first;
  then at the plan stage of a `2.1`/`2.2` item `plan_review_bundle_unbound`,
  and elsewhere `bundle_unverified` when computing **B** fails; then the
  pure predicate's order (`review_block_pinned`, `no_review_round`,
  `review_blocked`, `protected_path_dirty`,
  `implementation_provenance_stale`, `review_ledger_stale`).
- **The generation check** is `assert_local_generation_matches` over the
  stage's bundle `MANIFEST.md`: it refuses when the live worktree root or
  HEAD differs from the manifest's `worktree_root`/`generation_head` (a
  field the manifest does not record is not compared).
- **Pinned** is `is_technical_review_block_pinned(work_item, B)`.
- **The implementation bundle verifier** is
  `verify_implementation_review_bundle`, `/review-implementation`'s bundle
  check: the manifest is present, **B** can be computed and equals the
  manifest's `bundle_id`, the manifest is not a plan-stage one, and its
  `review_content_id` equals **I**.
- **The implementing entry status** is `implementing_entry_status`, the
  check behind `/milestone-implement`'s step 1a, with the causes
  `plan_approval_not_current`, `plan_approval_commit_unreachable` and
  `plan_content_drifted`.
- **Regenerate the implementation bundle** names the round's stage:
  `./scripts/prepare-ai-review.sh <base> <stage> <id>`, `<stage>`
  `implementation` at `implementation_revision` 1 and `post-fix` after.

### 6.3 Conditions and their calls

A row's condition is made of **calls** to Workflow functions. Every call
has exactly one of three **kinds**, and the kind decides what an exception
from it means:

- a **guard** call: its refusal *is* the condition. A listed class makes
  the condition true.
- an **acceptance** call: an `assert_*` used as a test. A listed class
  makes the call's sub-condition false, and evaluation continues as for
  any false condition.
- a **value** call: it returns the value the condition tests, and raises
  only on a data-integrity problem. A listed class names a reason.

Any other Workflow exception, from a call of any kind, ends the evaluation
at that row: the decision is `blocked`, `action` is `null`, and the reason
is `condition_refused`, with the exception's class and message in
`reason.text` and in `reason.native`. It is never a fall-through to a
later row and never an `ok: false` envelope. An exception that is not a
Workflow exception is not caught: it becomes `internal_error`.

Each row's calls, in evaluation order, mirrored by `CONDITION_CALLS`. A
row with no call has a single line of dashes. A row may declare one
function twice, once per kind, where one invocation's refusals divide
between a guard's classes and an acceptance's (rows 7a and 8a). A
computed value may be reused by a later row, and is still declared at
every row whose condition depends on it.

| row | call | kind | classes |
| --- | --- | --- | --- |
| 1 | `load_config` | value | — |
| 1b | `load_config` | value | — |
| 1a | `load_config` | value | — |
| 2 | — | — | — |
| 3 | — | — | — |
| 4 | — | — | — |
| 5 | `plan_review_publication_status` | guard | `PlanReviewBindingInconsistentError` |
| 6 | `assert_bundle_not_rejected` | guard | `BundleRejectedError` |
| 6a | — | — | — |
| 7 | — | — | — |
| 7a | `plan_review_publication_status` | value | — |
| 7a | `parse_review_feedback_header` | value | — |
| 7a | `assert_apply_plan_review_feedback` | acceptance | `FeedbackStatusNotApplicableError`, `FeedbackNotForConsumedContentError` |
| 7a | `assert_apply_review_feedback_binding` | guard | `MissingRequiredBundleFileError`, `ReviewBundleManifestMismatchError` |
| 7a | `assert_apply_review_feedback_binding` | acceptance | `FeedbackContentMismatchError`, `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 8 | `plan_review_publication_status` | value | — |
| 8 | `parse_review_feedback_header` | value | — |
| 8 | `assert_apply_plan_review_feedback` | acceptance | `FeedbackStatusNotApplicableError`, `FeedbackNotForConsumedContentError` |
| 8 | `assert_apply_review_feedback_binding` | acceptance | `FeedbackContentMismatchError`, `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 8a | `plan_review_publication_status` | value | — |
| 8a | `parse_review_feedback_header` | value | — |
| 8a | `assert_apply_plan_review_feedback` | acceptance | `FeedbackStatusNotApplicableError`, `FeedbackNotForConsumedContentError` |
| 8a | `assert_apply_review_feedback_binding` | guard | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 8a | `assert_apply_review_feedback_binding` | acceptance | `FeedbackContentMismatchError` |
| 9 | — | — | — |
| 10 | `plan_review_publication_status` | value | — |
| 11 | `parse_review_feedback_header` | value | — |
| 11 | `plan_review_publication_status` | value | — |
| 11a | `parse_review_feedback_header` | value | — |
| 11a | `plan_review_publication_status` | value | — |
| 11a | `assert_local_generation_matches` | guard | `WorktreeOrHeadMismatchError` |
| 12 | — | — | — |
| 13 | `parse_review_feedback_header` | value | — |
| 13 | `plan_review_publication_status` | value | — |
| 14 | — | — | — |
| 14a | `effective_policy` | value | — |
| 14a | `plan_approval_gate_status` | value | — |
| 14a | `evaluate_gate` | value | — |
| 14b | `effective_policy` | value | — |
| 14b | `plan_approval_gate_status` | value | — |
| 14b | `evaluate_gate` | value | — |
| 15 | `plan_approval_gate_status` | value | — |
| 16 | `plan_approval_gate_status` | value | — |
| 16a | `compute_bundle_id` | guard | `MissingRequiredBundleFileError` |
| 17 | `parse_review_feedback_header` | value | — |
| 17 | `compute_bundle_id` | value | — |
| 17 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 18 | `parse_review_feedback_header` | value | — |
| 18 | `compute_bundle_id` | value | — |
| 18 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 19 | `parse_review_feedback_header` | value | — |
| 19 | `compute_bundle_id` | value | — |
| 19 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 19 | `plan_approval_gate_status` | value | — |
| 20 | `parse_review_feedback_header` | value | — |
| 20 | `compute_bundle_id` | value | — |
| 20 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 20 | `plan_approval_gate_status` | value | — |
| 20a | `parse_review_feedback_header` | value | — |
| 20a | `compute_bundle_id` | value | — |
| 20a | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 20a | `plan_approval_gate_status` | value | — |
| 21 | — | — | — |
| 22 | `implementing_entry_status` | value | — |
| 23 | `registry_completion_status` | value | — |
| 23 | `select_next_checkpoint` | value | — |
| 24 | — | — | — |
| 25 | `parse_review_feedback_header` | value | — |
| 25 | `approval_review_content_id` | value | — |
| 25a | `parse_review_feedback_header` | value | — |
| 25a | `approval_review_content_id` | value | — |
| 25a | `assert_local_generation_matches` | guard | `WorktreeOrHeadMismatchError` |
| 25b | `verify_implementation_review_bundle` | guard | `ImplementationReviewBundleUnverifiedError` |
| 26 | — | — | — |
| 27 | `parse_review_feedback_header` | value | — |
| 27 | `approval_review_content_id` | value | — |
| 28 | — | — | — |
| 28a | `effective_policy` | value | — |
| 28a | `technical_approval_gate_status` | value | — |
| 28a | `evaluate_gate` | value | — |
| 28b | `effective_policy` | value | — |
| 28b | `technical_approval_gate_status` | value | — |
| 28b | `evaluate_gate` | value | — |
| 29 | `technical_approval_gate_status` | value | — |
| 30 | `technical_approval_gate_status` | value | — |
| 30a | `compute_bundle_id` | guard | `MissingRequiredBundleFileError` |
| 31 | `parse_review_feedback_header` | value | — |
| 31 | `compute_bundle_id` | value | — |
| 31 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 31 | `is_technical_review_block_pinned` | value | — |
| 31a | `parse_review_feedback_header` | value | — |
| 31a | `compute_bundle_id` | value | — |
| 31a | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 31a | `is_technical_review_block_pinned` | value | — |
| 32 | `parse_review_feedback_header` | value | — |
| 32 | `compute_bundle_id` | value | — |
| 32 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 33 | `parse_review_feedback_header` | value | — |
| 33 | `compute_bundle_id` | value | — |
| 33 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 33 | `technical_approval_gate_status` | value | — |
| 34 | `parse_review_feedback_header` | value | — |
| 34 | `compute_bundle_id` | value | — |
| 34 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 34 | `technical_approval_gate_status` | value | — |
| 35 | `parse_review_feedback_header` | value | — |
| 35 | `compute_bundle_id` | value | — |
| 35 | `assert_feedback_matches_bundle` | acceptance | `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 35 | `technical_approval_gate_status` | value | — |
| 35a | `parse_review_feedback_header` | value | — |
| 35a | `assert_apply_review_feedback_binding` | value | `MissingRequiredBundleFileError`, `ReviewBundleManifestMismatchError`, `FeedbackContentMismatchError`, `FeedbackBundleMismatchError`, `MissingFeedbackBindingFieldError` |
| 36 | — | — | — |
| 37 | `discover_current_functional_checklist_evidence` | value | — |
| 38 | `resolve_feedback_dir` | value | — |
| 38 | `assert_functional_review_not_already_consumed` | acceptance | `FunctionalReviewAlreadyAppliedError` |
| 38a | `resolve_own_registry_completion_status` | value | `StalePlanApprovalRegistryReadError`, `RegistryCoverageError` |
| 38b | `resolve_own_registry_completion_status` | value | — |
| 38c | `resolve_own_registry_completion_status` | value | — |
| 38d | `effective_policy` | value | — |
| 38d | `pr_query_trigger` | value | — |
| 38d | `actionable_pr_keys` | value | — |
| 38e | `effective_policy` | value | — |
| 38e | `evaluate_gate` | value | — |
| 38f | `effective_policy` | value | — |
| 38f | `evaluate_gate` | value | — |
| 38g | `effective_policy` | value | — |
| 38g | `evaluate_gate` | value | — |
| 38g | `pr_approved_requirements` | value | — |
| 38h | `effective_policy` | value | — |
| 38h | `evaluate_gate` | value | — |
| 38i | `effective_policy` | value | — |
| 38i | `evaluate_gate` | value | — |
| 39 | `resolve_own_registry_completion_status` | value | — |
| 40 | — | — | — |

### 6.4 The catalogue

The catalogue is total over every persisted phase and every supported
governing version (`gv`). **The printed order is the evaluation order**:
the rows are tried from top to bottom, across phases, and the first row
whose phase, `gv` and condition all match wins. No other precedence rule
exists. A row covers every listed phase at every listed `gv`, except where
the `gv` column says "as listed", when each phase names its own versions.
The no-item rows (1, 1b, 1a) apply only when no work item is named or active,
and only they do. Mirrored by `CATALOGUE`; the condition and notes columns
are a summary, and the code and its tests are the detail.

| row | phases | gv | condition | disposition | action | reason, invocation and notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | — | — | no work item is named or active, and `load_config`'s `default_workflow_version` is `2.2` (a two-stage default not in `NEW_WORK_RETIRED_VERSIONS`) | automatic | `plan.start` | `plan_start` |
| 1b | — | — | no work item is named or active, and `load_config`'s `default_workflow_version` is `2.1` (in `NEW_WORK_RETIRED_VERSIONS`) | blocked | — | `plan_start_default_retired`: a `/milestone-plan` would refuse with `DefaultWorkflowVersionRetiredError`; remedy `RETIRED_DEFAULT_NEXT_STEP` (set the default to `2.2` and commit it with `Workflow-Activation: 2.2`) |
| 1a | — | — | no work item is named or active (the default is `1`) | blocked | — | `plan_start_not_tracked`: a `1` `/milestone-plan` creates no work item |
| 2 | `AWAITING_TECHNICAL_APPROVAL`, `AWAITING_USER_ACCEPTANCE`, `FIXING_FUNCTIONAL_FINDINGS`, `SELF_REVIEWING_PLAN` | `1`, `2.1`, `2.2` | — (a vocabulary-only phase is persisted) | blocked | — | `invalid_state` |
| 3 | `LEGACY_READY` | `1`, `2.1`, `2.2` | — | blocked | — | `legacy_item_not_activated`; remedy: promote it, or (protocol `1.2`) retire it as already finished; alternative `legacy.retire` (`user_only`, never automatic) |
| 4 | `AWAITING_EXTERNAL_PLAN_REVIEW` at `2.1`/`2.2`, `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` at `1`/`2.1`, `AWAITING_LOCAL_PLAN_REVIEW` at `1`, `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` at `1`/`2.1`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` at `1`, `AWAITING_PLAN_APPROVAL` at `1`, `REVISING_PLAN` at `1`, `SELF_REVIEWING_IMPLEMENTATION` at `1` | as listed | — (no writer persists the phase at that `gv`) | blocked | — | `phase_not_legal_for_governing_version` |
| 5 | `AMENDING_PLAN`, `AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL`, `PLANNING`, `REVISING_PLAN` | `2.1`, `2.2` | the publication status raises `PlanReviewBindingInconsistentError` | blocked | — | `plan_review_binding_inconsistent`, the error in the text; remedy: withdraw with `/milestone-plan <id>` at a ready phase (alternative `plan.withdraw`), otherwise none exists (repair the record by hand) |
| 6 | `APPLYING_REVIEW_FEEDBACK`, `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `AWAITING_EXTERNAL_PLAN_REVIEW`, `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, `AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL`, `REVISING_PLAN` | `1`, `2.1`, `2.2` | `assert_bundle_not_rejected` refuses | blocked | — | `bundle_rejected`; the marker's detail; at a two-stage plan phase, alternative `plan.withdraw` |
| 6a | `AMENDING_PLAN`, `IMPLEMENTING`, `PLANNING` | `1` | — | blocked | — | `v1_state_not_advanced`; at `IMPLEMENTING` the `1` `/milestone-implement` runs by hand (remedy `/milestone-implement`, the orchestrator does not drive governing `1`); at `PLANNING`/`AMENDING_PLAN` no `1` command advances the state (`v2.6.0-003`) |
| 7 | `AMENDING_PLAN`, `PLANNING` | `2.1`, `2.2` | — | automatic | `plan.author` | `/milestone-plan <id>` |
| 7a | `REVISING_PLAN` | `2.1`, `2.2` | fb passes `assert_apply_plan_review_feedback` in `"bundle"` mode, and `assert_apply_review_feedback_binding` raises a bundle-integrity class | blocked | — | `bundle_unverified`: restore the reviewed plan bundle; alternative `plan.withdraw` |
| 8 | `REVISING_PLAN` | `2.1`, `2.2` | fb passes `/apply-plan-review`'s step-1 acceptance (by content for a `REVISE` that states a `review_content_id`, by bundle otherwise) | automatic | `plan.apply_review` | `/apply-plan-review <id>` |
| 8a | `REVISING_PLAN` | `2.1`, `2.2` | fb is a `REVISE` naming this item that the content binding cannot bind, and the bundle binding refuses it | blocked | — | `review_feedback_unbound`: a new verdict for **B**; alternative `plan.withdraw` |
| 9 | `REVISING_PLAN` | `2.1`, `2.2` | — (a withdrawal, or no applicable `REVISE`) | automatic | `plan.author` | `/milestone-plan <id>` |
| 10 | `AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` | publication status `CONTENT_DRIFTED`, `BUNDLE_UNVERIFIED` or `LEGACY_UNVERIFIED` | blocked | — | the status lowercased (`content_drifted`, `bundle_unverified`, `legacy_unverified`), with its remedy; alternative `plan.withdraw` |
| 11 | `AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `2.1`, `2.2` | fb is current and `BLOCK` | human_gate | `review.resolve_block` | `review_blocked`; alternative `plan.withdraw` |
| 11a | `AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `2.1`, `2.2` | at the local phase, or at the manual phase with an unrecorded manual verdict: the generation check refuses | blocked | — | `bundle_generation_mismatch`; alternative `plan.withdraw` |
| 12 | `AWAITING_LOCAL_PLAN_REVIEW` | `2.1`, `2.2` | — | automatic | `plan.review.local` | `/review-plan <id>`, fresh session, independent of `planner` |
| 13 | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `2.1`, `2.2` | fb holds an unrecorded manual verdict | automatic | `plan.record_external` | `/record-manual-plan-review <id>` |
| 14 | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `2.1`, `2.2` | — | external_gate | `plan.review.external` | `satisfied_by: plan_review_verdict` |
| 14a | `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` | the plan gate is `automatic` for the item (`1.1`), its status is reachable and `evaluate_gate` is satisfiable | validation | `plan.satisfy` | `policy_satisfiable`; `/satisfy-gate plan <id>`, role `validator`; the decision carries a `policy` object |
| 14b | `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` | the plan gate is `automatic`, its status is reachable and `evaluate_gate` is not satisfiable | blocked | — | `gate_evidence_unmet`: each unmet requirement; remedy: turn the plan gate human and run `/approve-review plan <id>`, or (for `distinct_reviewer_models`, `review_evidence_audited`) withdraw with `/milestone-plan <id>`; an unreachable wrapper reaches row 16 unchanged |
| 15 | `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` | the plan gate status is reachable | human_gate | `plan.approve` | `/approve-review plan <id>`, `user_only` |
| 16 | `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` | — (the plan gate is unreachable) | blocked | — | the gate's cause; alternative `plan.withdraw` |
| 16a | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` | computing **B** raises `MissingRequiredBundleFileError` | blocked | — | `bundle_unverified`: regenerate the plan bundle |
| 17 | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` | fb is current and `BLOCK` | automatic | `plan.apply_review` | `review_blocked`; the `1` apply handles a `BLOCK` |
| 18 | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` | fb is current and `REVISE` | automatic | `plan.apply_review` | `/apply-plan-review <id>` |
| 19 | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` | fb is current and `APPROVE`, and the plan gate status is reachable | human_gate | `plan.approve` | `/approve-review plan <id>`, `user_only` |
| 20 | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` | fb is an applied `REVISE` (not current, `Status: REVISE`), and the plan gate status is reachable | human_gate | `plan.approve` | `/approve-review plan <id>` (`USER_OVERRIDE`); alternative `plan.review.external` |
| 20a | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` | as row 19 or row 20, with the plan gate unreachable | blocked | — | the gate's cause; alternative `plan.review.external` |
| 21 | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` | — (no fb, or a verdict for an earlier round) | external_gate | `plan.review.external` | `satisfied_by: plan_review_verdict` |
| 22 | `IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION` | `2.1`, `2.2` | the implementing entry status is not reachable | blocked | — | its cause: `plan_approval_not_current`, `plan_content_drifted` or `plan_approval_commit_unreachable` |
| 23 | `IMPLEMENTING` | `2.1`, `2.2` | the registry is incomplete | automatic | `implementation.checkpoint` | `/milestone-implement <id>`; `arguments.checkpoint_id` is `select_next_checkpoint` |
| 24 | `IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION` | `2.1`, `2.2` | — | automatic | `implementation.self_review` | `/milestone-implement <id>`, role `self_reviewer` |
| 25 | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | fb is current and `BLOCK` | human_gate | `review.resolve_block` | `review_blocked` |
| 25a | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | at the local phase, or at the manual phase with an unrecorded manual verdict: the generation check refuses | blocked | — | `bundle_generation_mismatch`; alternative `implementation.recover_provenance` |
| 25b | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | the implementation bundle verifier refuses | blocked | — | `bundle_unverified`: regenerate the implementation bundle |
| 26 | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` | — | automatic | `implementation.review.local` | `/review-implementation <id>`, fresh session, independent of `implementer` and `self_reviewer` |
| 27 | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | fb holds an unrecorded manual verdict | automatic | `implementation.record_external` | `/record-manual-implementation-review <id>` |
| 28 | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | — | external_gate | `implementation.review.external` | `satisfied_by: implementation_review_verdict` |
| 28a | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | the technical gate is `automatic`, its status is reachable and `evaluate_gate` is satisfiable | validation | `implementation.satisfy` | `policy_satisfiable`; `/satisfy-gate implementation <id>`, role `validator`; carries a `policy` object |
| 28b | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | the technical gate is `automatic`, its status is reachable and `evaluate_gate` is not satisfiable | blocked | — | `gate_evidence_unmet`: each unmet requirement; remedy: turn the technical gate human and run `/approve-review implementation <id>`, or withdraw with `/apply-implementation-review <id>`; an unreachable wrapper reaches row 30 unchanged |
| 29 | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | the technical gate status is reachable | human_gate | `implementation.approve` | `/approve-review implementation <id>`, `user_only` |
| 30 | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | — (the technical gate is unreachable) | blocked | — | the gate's cause, with its remedy and alternatives |
| 30a | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` | computing **B** raises `MissingRequiredBundleFileError` | blocked | — | `bundle_unverified`: regenerate the implementation bundle |
| 31 | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` | fb is current, and `BLOCK` or pinned | automatic | `implementation.apply_review` | `review_blocked` or `review_block_pinned` |
| 31a | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` | pinned, with no current fb | external_gate | `implementation.review.external` | `review_block_pinned`; `satisfied_by: implementation_review_verdict` |
| 32 | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` | fb is current and `REVISE` | automatic | `implementation.apply_review` | `/apply-implementation-review <id>` |
| 33 | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` | fb is current and `APPROVE`, and the technical gate status is reachable | human_gate | `implementation.approve` | `/approve-review implementation <id>`, `user_only` |
| 34 | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` | fb is current and `APPROVE` (the technical gate is unreachable) | blocked | — | the gate's cause; alternative `implementation.recover_provenance` for `bundle_generation_mismatch`, `protected_path_dirty` and `implementation_provenance_stale` |
| 35 | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` | — (no current fb) | external_gate | `implementation.review.external` | `satisfied_by: implementation_review_verdict`; alternative `implementation.review.local`, and `implementation.approve` after an applied `REVISE` when the technical gate is reachable |
| 35a | `APPLYING_REVIEW_FEEDBACK` | `1`, `2.1`, `2.2` | no fb, or `assert_apply_review_feedback_binding` refuses fb | blocked | — | `review_feedback_missing`, `bundle_unverified` or `review_feedback_not_current` |
| 36 | `APPLYING_REVIEW_FEEDBACK` | `1`, `2.1`, `2.2` | — | automatic | `implementation.apply_review` | `/apply-implementation-review <id>` |
| 37 | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | no current checklist evidence | automatic | `functional.prepare` | `/prepare-functional-review <id>` |
| 38 | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | an unconsumed `FUNCTIONAL_REVIEW.md` exists | automatic | `functional.apply_findings` | `/apply-functional-review <id>` |
| 38a | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | `resolve_own_registry_completion_status` raises | blocked | — | `plan_content_drifted` or `registry_unreadable`; alternative `functional.review.advisory` |
| 38b | `AWAITING_FUNCTIONAL_REVIEW` | `1` | the registry is not terminal | blocked | — | `v1_state_not_advanced`: the item cannot be accepted until its registry is terminal (the open residual of `v2.6.0-003`) |
| 38c | `AWAITING_FUNCTIONAL_REVIEW` | `2.1`, `2.2` | the registry is not terminal | blocked | — | `registry_incomplete`: no command completes it here; hand-constructed state only; remedy `/resume-implementation <id>`, alternative `implementation.resume` (`user_only`, never automatic, protocol `1.2`) |
| 38d | `AWAITING_FUNCTIONAL_REVIEW`, `MILESTONE_COMPLETE` | `1`, `2.1`, `2.2` | the query trigger holds (a reported pull-request fact differs from the stored `workflow_gh` fact), or the stored `workflow_gh` fact has an unapplied cause actionable under the policy; no gate-mode condition | automatic | `pr.apply_review` | `pr_query_due` or `pr_review_actionable`; `/apply-pr-review <id>`; its first step is the Workflow's own query; matches a completed item too, except a retired legacy item (`is_retired_legacy_item`: `MILESTONE_COMPLETE`, governing `1`, `LEGACY_V1`), which no row matches for it and which is never reopened (`reopen_retired_legacy_item`) |
| 38e | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | the acceptance gate is `automatic`, its checkpoints and technical approval are current, and `functional_flows_passed` is unmet for want of evidence | external_gate | `functional.evidence.external` | `functional_evidence_needed`; `satisfied_by: functional_evidence` |
| 38f | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | automatic acceptance, the stored `workflow_gh` fact at least as new as the reported one, and `pr_fact_current` or `ci_green` unmet with a pull-request fact obtainable | external_gate | `pr.review.external` | `pr_evidence_needed`; `satisfied_by: pr_review_result` is a trigger, never evidence |
| 38g | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | `requires_pr_approved`, the stored `workflow_gh` fact at least as new as the reported one, and `pr_approved` unmet (automatic or human acceptance) | external_gate | `pr.review.external` | `pr_approval_needed`; for a human gate the remedy names `/accept-milestone <id>` |
| 38h | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | automatic acceptance, satisfiable, or satisfiable once the pending query is run | validation | `acceptance.satisfy` | `policy_satisfiable`; `/satisfy-gate acceptance <id>`, role `validator`; its act re-queries GitHub and may still refuse |
| 38i | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | automatic acceptance, not satisfiable, nothing obtainable | blocked | — | `gate_evidence_unmet`: each unmet requirement; remedy: `/apply-functional-review <id>`, or turn the acceptance gate human and `/accept-milestone <id>` |
| 39 | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` | — (the registry is terminal) | human_gate | `functional.review` | alternatives `milestone.accept` (`user_only`), `functional.apply_findings`, `functional.review.advisory` |
| 40 | `MILESTONE_COMPLETE` | `1`, `2.1`, `2.2` | — | complete | — | — |

Notes on the catalogue:

- **Row `1b` is evaluated before row `1a`.** Its letter sorts after `1a`, but
  `1a` is the unconditional fall-through for the `1` default and row ids are
  stable, so row `1b` sits between rows `1` and `1a`. The order is deliberate
  and `PRINTED_ROW_ORDER` pins it; without row `1b` a `2.1` default would keep
  getting `plan.start` for a run that refuses.
- **Rows 38 and 38d after a refused broad branch.** When the broad branch of
  `functional.apply_findings` (row 38) or `pr.apply_review` (row 38d) is refused
  with `DefaultWorkflowVersionRetiredError`, the phase is unchanged and
  `reconcile` classifies the run `no_progress`; the next `next-action` matches
  the same row. A repeated `no_progress` from row 38 or 38d while `verify`
  reports the `default_workflow_version` `warn` is that refusal, not a stall to
  retry: the remedy is the operator's configuration edit
  (`RETIRED_DEFAULT_NEXT_STEP`). No row pre-empts it, because the broad/bounded
  classification is the worker's, made by reading the findings.

- **`1` items.** The protocol drives a `1` item only once it already has a
  `work_items` entry (a remediation child created under a `1` default, a
  legacy import, the bootstrap's item); no `1` command creates one, so a
  `1` default gives row 1a, never `plan.start`. Two `1` states cannot be
  advanced by any `1` command, and row 6a reports them as `blocked`:
  `PLANNING`/`AMENDING_PLAN` (`/milestone-plan`'s `1` branch writes no
  state) and `IMPLEMENTING` (from 2.9.0 the hand-run `/milestone-implement`
  reaches review, since `record_bundle_generation` accepts `implementation`
  from `IMPLEMENTING` for a governing-`1` item; the orchestrator still does
  not drive `1`, so row 6a names that command as the remedy). At the
  functional gate, row 38b reports a `1` item whose registry is not
  terminal, which `/accept-milestone` can never accept. Row 38c is the
  `2.1`/`2.2` counterpart, reachable only from a hand-constructed or
  hand-edited state with a `CURRENT` plan approval (a promoted legacy item
  has none, so row 38a reports it first). No command completes an
  outstanding checkpoint from `AWAITING_FUNCTIONAL_REVIEW`; from 2.9.0 the
  user-only `/resume-implementation` returns the item to `IMPLEMENTING`
  with its technical approval `STALE`, reported as the `implementation.resume`
  alternative of row 38c. All three are the defect `v2.6.0-003`, reported
  in 2.7.0 and addressed in 2.9.0 as the earlier rows describe.
- **`BLOCK`.** Only a two-stage `BLOCK` (rows 11 and 25) is a gate: no
  command applies it, and it needs the user's resolution. A `1`/`2.1`
  `BLOCK` (rows 17 and 31) is applied by `/apply-plan-review` or
  `/apply-implementation-review`, exactly as a `REVISE` is.
- **Applied `1` verdicts.** `/apply-plan-review` regenerates the plan
  bundle, so an applied `REVISE` no longer matches **B** and is not
  current: row 18 is never re-emitted, and row 20 offers the approval
  gate (basis `USER_OVERRIDE`) or another external round. Row 35 is its
  implementation-stage counterpart.
- **A recorded `REVISE` is applied by content.** A two-stage `REVISE` that
  states the consumed `review_content_id` gives row 8 whatever its bundle
  fields say; row 8a blocks only a `REVISE` the content binding cannot
  bind (a legacy marker, a `REVISE` stating no `review_content_id`, or a
  bundle manifest naming no work item). With no fb at all at
  `REVISING_PLAN`, row 9 re-authors: the state cannot tell that from a
  withdrawal, and the re-authored content still needs both reviews of its
  own. `APPLYING_REVIEW_FEEDBACK` differs: row 35a blocks on no fb.
- **Bundle integrity.** At the apply phases (rows 7a and 35a) the remedy
  is to restore the reviewed bundle, never to regenerate it: the feedback
  binds to the reviewed bundle, and at `APPLYING_REVIEW_FEEDBACK` a
  generation is the round's exit. `.ai-review/` is untracked while the
  state is tracked, so a fresh clone or a second worktree has the state and
  no bundle; rows 7a, 16a, 25b, 30a and 35a report that instead of looping.
- **Rows 25a and 25b** are ordered on purpose: a generation mismatch has
  the narrower remedy (`/recover-implementation-provenance`, no new round).
  Both block, so no automatic action depends on the order.
- **Rows 38a, 38b and 38c** follow rows 37 and 38: preparing the checklist
  and applying unconsumed functional findings do not need a terminal
  registry, so a drifted plan or a non-terminal registry is reported only
  where acceptance would be offered.
- **`validation`** is in the v1 vocabulary. 2.7.0 emits it from no row;
  from protocol `1.1` (Workflow 2.8.0) rows `14a`, `28a` and `38h` emit it:
  its action (`plan.satisfy`, `implementation.satisfy`,
  `acceptance.satisfy`, role `validator`) is launched like an `automatic`
  one, and `reconcile` accepts such a decision.

## 7. `reconcile`: legal edges and classification

**The item reconciled** is the decision's `basis.work_item_id`. For
`plan.start`, which has no basis, it is the new `active_work_item_id`: it
must not be in the decision's `snapshot.work_item_ids`, and exactly one key
of `work_items` may be new. `from` is then `null`. When no key is new and
`active_work_item_id` is still null, the class is `no_progress` and `to`
is `null` too (an interrupted `/milestone-plan`). Every other outcome is
`invalid` with reason `plan_start_item_ambiguous`.

Each automatic action has a fixed set of legal edges (`from → to`, by
`gv`; `none` is "no work item"). An edge whose target is `none` is legal
at any `gv`. Mirrored by `EDGES`:

| action id | from | to | gv |
| --- | --- | --- | --- |
| `plan.start` | none | `PLANNING` | `2.1`, `2.2` |
| `plan.start` | none | `AWAITING_LOCAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `plan.start` | none | none | `2.1`, `2.2` |
| `plan.author` | `PLANNING` | `AWAITING_LOCAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `plan.author` | `AMENDING_PLAN` | `AWAITING_LOCAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `plan.author` | `REVISING_PLAN` | `AWAITING_LOCAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `plan.author` | `PLANNING` | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` |
| `plan.author` | `AMENDING_PLAN` | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` |
| `plan.author` | `PLANNING` | `PLANNING` | `2.1`, `2.2` |
| `plan.author` | `AMENDING_PLAN` | `AMENDING_PLAN` | `2.1`, `2.2` |
| `plan.author` | `REVISING_PLAN` | `REVISING_PLAN` | `2.1`, `2.2` |
| `plan.author` | `PLANNING` | `PLANNING` | `1` |
| `plan.author` | `AMENDING_PLAN` | `AMENDING_PLAN` | `1` |
| `plan.apply_review` | `REVISING_PLAN` | `AWAITING_LOCAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `plan.apply_review` | `REVISING_PLAN` | `REVISING_PLAN` | `2.1`, `2.2` |
| `plan.apply_review` | `AWAITING_EXTERNAL_PLAN_REVIEW` | `AWAITING_EXTERNAL_PLAN_REVIEW` | `1` |
| `plan.review.local` | `AWAITING_LOCAL_PLAN_REVIEW` | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `plan.review.local` | `AWAITING_LOCAL_PLAN_REVIEW` | `REVISING_PLAN` | `2.1`, `2.2` |
| `plan.review.local` | `AWAITING_LOCAL_PLAN_REVIEW` | `AWAITING_LOCAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `plan.record_external` | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` |
| `plan.record_external` | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `REVISING_PLAN` | `2.1`, `2.2` |
| `plan.record_external` | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `2.1`, `2.2` |
| `implementation.checkpoint` | `IMPLEMENTING` | `IMPLEMENTING` | `2.1`, `2.2` |
| `implementation.checkpoint` | `IMPLEMENTING` | `SELF_REVIEWING_IMPLEMENTATION` | `2.1`, `2.2` |
| `implementation.self_review` | `IMPLEMENTING` | `SELF_REVIEWING_IMPLEMENTATION` | `2.1`, `2.2` |
| `implementation.self_review` | `IMPLEMENTING` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `implementation.self_review` | `SELF_REVIEWING_IMPLEMENTATION` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `implementation.self_review` | `IMPLEMENTING` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.1` |
| `implementation.self_review` | `SELF_REVIEWING_IMPLEMENTATION` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.1` |
| `implementation.self_review` | `IMPLEMENTING` | `IMPLEMENTING` | `2.1`, `2.2` |
| `implementation.self_review` | `SELF_REVIEWING_IMPLEMENTATION` | `SELF_REVIEWING_IMPLEMENTATION` | `2.1`, `2.2` |
| `implementation.review.local` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `implementation.review.local` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `APPLYING_REVIEW_FEEDBACK` | `2.2` |
| `implementation.review.local` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `implementation.record_external` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `implementation.record_external` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `APPLYING_REVIEW_FEEDBACK` | `2.2` |
| `implementation.record_external` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `implementation.apply_review` | `APPLYING_REVIEW_FEEDBACK` | `APPLYING_REVIEW_FEEDBACK` | `1`, `2.1`, `2.2` |
| `implementation.apply_review` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `APPLYING_REVIEW_FEEDBACK` | `1`, `2.1` |
| `implementation.apply_review` | `APPLYING_REVIEW_FEEDBACK` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `implementation.apply_review` | `APPLYING_REVIEW_FEEDBACK` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` |
| `implementation.apply_review` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1`, `2.1` |
| `functional.prepare` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` |
| `functional.apply_findings` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` |
| `functional.apply_findings` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1` |
| `functional.apply_findings` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.1` |
| `functional.apply_findings` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `plan.satisfy` | `AWAITING_PLAN_APPROVAL` | `IMPLEMENTING` | `2.1`, `2.2` |
| `plan.satisfy` | `AWAITING_PLAN_APPROVAL` | `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` |
| `implementation.satisfy` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `AWAITING_FUNCTIONAL_REVIEW` | `2.2` |
| `implementation.satisfy` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `acceptance.satisfy` | `AWAITING_FUNCTIONAL_REVIEW` | `MILESTONE_COMPLETE` | `1`, `2.1`, `2.2` |
| `acceptance.satisfy` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` |
| `pr.apply_review` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` |
| `pr.apply_review` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `1` |
| `pr.apply_review` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.1` |
| `pr.apply_review` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` |
| `pr.apply_review` | `MILESTONE_COMPLETE` | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` |
| `pr.apply_review` | `MILESTONE_COMPLETE` | `MILESTONE_COMPLETE` | `1`, `2.1`, `2.2` |

Each automatic action's same-phase proof of progress and its
`allowed_results`, which `next-action` copies into
`action.allowed_results`. Mirrored by `EDGES`:

| action id | same-phase proof | allowed_results |
| --- | --- | --- |
| `plan.start` | — | `progress`, `gate_reached`, `no_progress` |
| `plan.author` | — | `progress`, `gate_reached`, `no_progress` |
| `plan.apply_review` | `plan_revision_advanced` | `progress`, `gate_reached`, `no_progress` |
| `plan.review.local` | — | `progress`, `gate_reached`, `no_progress` |
| `plan.record_external` | — | `progress`, `gate_reached`, `no_progress` |
| `implementation.checkpoint` | `checkpoint_completed` | `progress`, `no_progress` |
| `implementation.self_review` | — | `progress`, `gate_reached`, `no_progress` |
| `implementation.review.local` | — | `progress`, `gate_reached`, `no_progress` |
| `implementation.record_external` | — | `progress`, `gate_reached`, `no_progress` |
| `implementation.apply_review` | `implementation_revision_advanced` | `progress`, `gate_reached`, `no_progress` |
| `functional.prepare` | — | `gate_reached`, `no_progress` |
| `functional.apply_findings` | — | `progress`, `gate_reached`, `no_progress` |
| `plan.satisfy` | — | `progress`, `gate_reached`, `no_progress` |
| `implementation.satisfy` | — | `progress`, `gate_reached`, `no_progress` |
| `acceptance.satisfy` | — | `progress`, `gate_reached`, `no_progress` |
| `pr.apply_review` | — | `progress`, `gate_reached`, `no_progress` |

The proofs:

- `plan_revision_advanced`: the item is `1` and its `plan_revision`
  exceeds the decision's `snapshot.plan_revision`;
- `checkpoint_completed`: at least one checkpoint is `COMPLETE` that was
  not in `basis.checkpoints`, and `last_completed_checkpoint_id` is one of
  them;
- `implementation_revision_advanced`: `implementation_revision` exceeds
  the decision's `snapshot.implementation_revision`.

Classification, first match wins:

1. **`invalid`**, with each reason in `invalid_reasons`:
   - `state_invalid`: `validate_state` refuses. This reason stands alone,
     and `from`, `to`, `basis` and `next` are `null`;
   - `illegal_edge`: the edge is not among the action's legal edges;
   - `checkpoint_completion_unproven`: a checkpoint is `COMPLETE` that was
     not in `basis.checkpoints`, and `prove_checkpoint_completions` (the
     proof of `verify` check 5, descent from the recorded `start_commit`
     included) cannot prove it by a reachable trailer commit;
   - `bundle_rejected`: the new phase is a review phase (row 6's seven
     review phases, not its two apply phases) and
     `assert_bundle_not_rejected` refuses;
   - `plan_review_not_bound`: at `2.1`/`2.2`, the new phase is a ready
     plan phase (`AWAITING_LOCAL_PLAN_REVIEW`,
     `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL`) and
     `plan_review_publication_status` is not `BOUND`.
2. **`gate_reached`**: the new decision's disposition is `human_gate` or
   `external_gate`. That includes an unchanged state after a `BLOCK`
   verdict, which writes no state.
3. **`progress`**: a phase change, or an unchanged phase whose proof
   holds.
4. **`no_progress`**: everything else on a legal edge, such as an
   unchanged state, or a checkpoint moved to `IN_PROGRESS` and not
   completed (`evidence.started_checkpoints` names it).

A class not in the action's `allowed_results` is then replaced by
`invalid` with reason `result_not_allowed`. `evidence.recorded_stage`
names the review stage a review or record action recorded when it moved
the phase.

**The classes of the gate-policy actions (protocol `1.1`).** The three
`.satisfy` actions and `pr.apply_review` take `proof: —` and the
classifier's own terms above; nothing in the classifier changed. A refusal of
a `.satisfy` act leaves the phase alone and reconciles through its same-phase
edge, never `illegal_edge`. The class is decided by where the next decision
lands:

- `progress`: the act reached its forward target (the phase advanced), or
  `pr.apply_review` reopened a completed item (`MILESTONE_COMPLETE` to
  `AWAITING_FUNCTIONAL_REVIEW`) or took a branch that changed the phase;
- `gate_reached`: the next decision is a human or external gate. That is
  `38f` or `38g` (external) after an `acceptance.satisfy` refusal that stored
  a pending or not-current fact, and row 15, 29 or 39 (human) after a refusal
  because the gate was turned human meanwhile;
- `no_progress`: everything else on a legal edge, for example `38h` again
  after `forge_unavailable` (nothing is stored), `38i` after a stored
  failed-checks or `CHANGES_REQUESTED` fact, `14b` or `28b` after a
  `plan.satisfy` or `implementation.satisfy` refusal, `38d` after a stored red
  fact that reopened the item, and `pr.apply_review` ending in a refusal
  (`pr_merged`, `pr_fact_superseded`, `forge_unavailable`,
  `forge_undecidable`, `pr_head_unknown`, `pr_head_not_in_branch`) or in the
  recorded result `pr_fact_refreshed` at a completed item, which reconciles
  over the unchanged `MILESTONE_COMPLETE` edge.

A `38d` or `38i` ending changes the stored facts and so the next action's id
and `state_identity`, yet still reconciles `no_progress`: that is correct for
a `1.0` consumer too.

**Completion is reported by `next-action`; `reconcile` has no `complete`
class.** Where the acceptance gate is human, the only writer of
`MILESTONE_COMPLETE` is `/accept-milestone`, a `user_only` action offered at
the `human_gate` row 39. Where it is automatic (the default of protocol
`1.1`), `acceptance.satisfy` is a `validation` action (role `validator`,
launched like an automatic one) and the only action with an edge from
`AWAITING_FUNCTIONAL_REVIEW` to `MILESTONE_COMPLETE`; its arrival there is
classed `progress`, not a completion class. After either the next `next-action` returns row 40,
disposition `complete`, and that is the terminal signal. `/accept-milestone`
stays the writer only for a human gate. Any other action that moved an item into `MILESTONE_COMPLETE` would be
`invalid` (`illegal_edge`).

## 8. Consumer obligations

A consumer of protocol `1.x`:

1. passes `--protocol-major 1` on every call, and treats
   `unsupported_protocol` as fatal;
2. validates every envelope and `result` against the schema of the major
   it supports, ignoring unknown response fields;
3. fails closed on an unknown value: an unknown action id, disposition or
   error code is `blocked`, never success, and an unknown governing
   version or phase is never driven;
4. dispatches on `action.id` and `arguments`, never on `invocation`, and
   honours `worker` (role, a fresh session, independence, `user_only`);
5. runs an `automatic` action (and, from protocol `1.1`, a `validation`
   action) only, never a gate's action or an alternative on its own; a
   `user_only` action is always a person's (a verdict courier at an
   external gate is admissible only under the conditions of section 8.2);
6. checks the identity before launching: `next-action
   --expect-state-identity <basis.state_identity>` immediately before
   the launch, re-deciding on `stale_decision`;
7. passes the executed decision back to `reconcile` verbatim, and calls
   `next-action` again after a gate is resolved or an external result is
   recorded, never `reconcile`;
8. records external verdicts through `record-external-result` only, and
   never reads or writes a Workflow artifact path to drive the lifecycle
   (`resolve-artifact` is for display and hand-off; section 8.2 states when
   a courier may carry a verdict);
9. stops at disposition `complete`;
10. (protocol `1.1`, a recommendation) bounds its consecutive `no_progress`
    results of the **same** action id at an **unchanged** `state_identity`,
    then treats the item as `blocked` with the last refusal's message. This
    is not a bound on any `no_progress` result: an ending that changes the
    stored facts, such as a `38d` or `38i` one, changes `state_identity` and
    hands off to `pr.apply_review` or to a `blocked` explanation. A
    refusal that stores nothing (`forge_unavailable` at `38h`, or an unsafe
    `gh` path) leaves `state_identity` unchanged and is the case the bound is
    for. Section 8 defined no such limit in `1.0`.

### 8.1 A round cap (protocol `1.4`, per role from `1.5`)

An orchestrator with a round cap compares the number for the stage and role it
is about to dispatch (`review_round.plan_local`, `plan_external`,
`implementation_local` or `implementation_external` from protocol `1.5`, else
`review_round[<stage>]`; section 5.4) with its cap before dispatching a review
or an apply action and, at the cap, stops dispatching and hands the item to a
person. The stage and role to compare follow `review_round.current`. When the
field is `null` (not derivable, including a field that was a number earlier) it
counts the rounds itself, for example by its own review-job count for the item,
and never treats `null` as 0 or as any number. Two ways a number can mislead:
**missing** (`null`: an item created before 2.15.0, a promoted legacy item, a
feedback-only item or a malformed key; the consumer counts itself) and
**stale** (a number that reads low after a verdict was ingested while the
repository ran an earlier release; undetectable, so a consumer holding a hard
cap on an item that was ever moved back counts itself). A number is exact for an item
whose plan-review rounds all ran under Workflow 2.7.0 or later and advisory
for cap purposes otherwise: a consumer that must enforce a hard cap on an
item that predates 2.7.0, or on any item of a repository ever moved back
below 2.7.0, counts the rounds itself.

The Workflow never accepts at a cap. What the person can do is the existing
user-only paths, unchanged: `/approve-review` with the confirmation guard
where the gate is reachable (`USER_OVERRIDE` after a `REVISE`); otherwise
continue the loop, or withdraw and re-scope the plan (`/milestone-plan
<id>`).

### 8.2 A distinct-provider verdict courier at an `external_gate`

A clarification of obligations 5 and 8 and of sections 5.5 and 5.6, with no
behavior change, field, row, action or check. Independence is enforced by the
Workflow, not by trust in the courier: `record-external-result` runs every
guard (binding, content id, stage preconditions, `distinct_reviewer_models`
under automatic gates). A courier is admissible when, and only when:

1. the verdict is produced by a different provider and model family from the
   local stage's reviewer (stated in its `Reviewer model:` line), in a
   disposable copy with no write access to the real repository;
2. the courier passes the verdict text verbatim, never edits, filters or
   retries it into a different status, and records every verdict it obtains,
   `REVISE` included; a limit or failure retry re-runs only an attempt that
   produced no verdict;
3. entry is through `record-external-result` only; on a refusal the
   orchestrator re-decides with `next-action` and never works around the
   guard;
4. it couriers only the two verdict kinds at rows 14 and 28, never resolves a
   `user_only` action, and under a human gate policy the approval stays a
   person's (recording a verdict is not an approval);
5. it is enabled by the repository owner's configuration or by an explicit Controller
   default the owner accepted at setup; unset, the gate stops as today.

## 9. Reserved for a later protocol version

- Nothing is reserved in `1.1`: the disposition `validation` and the result
  kinds `functional_evidence` and `pr_review_result`, reserved in `1.0`, are
  in use. `reserved_result_kinds` in `describe` is empty.

## 10. Compatibility notes

- **Protocol `1.5`** (Workflow 2.15.0; Workflow 2.16.0, 2.17.0, 2.18.0, 2.18.1, 2.18.2, 2.18.3, 2.18.4, 2.18.5, 2.18.6, 2.18.7, 2.19.0, 2.19.1, 2.19.2 and 2.20.0 keep it: no snapshot field,
  row, action, envelope key or classification changed). The `review_round` object of an item's
  `next-action` snapshot gains four additive properties after `current`:
  `plan_local`, `plan_external`, `implementation_local` and
  `implementation_external`, each an integer `>= 0` or `null` (section 5.4),
  all required in the `1.5` schema. `plan`, `implementation` and `current` are
  unchanged. No row, action, reason code, check id, envelope key or error code
  changed; `verify`'s `state_valid` check also reports a malformed counter
  (section 5.2). A `1.4` consumer ignores the unknown fields (obligation 2) and
  reads `plan`, `implementation` and `current` exactly as before, but the `1.4`
  schema's `review_round` object has `additionalProperties: false`, so a
  consumer that validates a `1.5` response against its own copy of the `1.4`
  schema rejects it: validate against the `1.5` schema, or drop unknown
  `review_round` fields first. A `1.5` consumer reading a `1.4` response (an
  older Workflow) finds the four fields absent and treats each as `null`,
  counting the rounds itself. Section 8.1 (the round cap) is consumer guidance
  only.
- **Protocol `1.4`** (Workflow 2.12.0; Workflow 2.13.0 and Workflow 2.14.0 keep it: no snapshot field,
  row, action, envelope key or classification changed). The snapshot of an item's `next-action`
  decision gains the additive `review_round` field (section 5.4). No row,
  action, reason code, check or error code changed. A `1.3` consumer ignores
  the unknown field (obligation 2), but the `1.3` schema's `snapshot` object
  has `additionalProperties: false`, so a consumer that validates a `1.4`
  response against its own copy of the `1.3` schema rejects it: validate
  against the `1.4` schema, or drop unknown snapshot fields before
  validating. Section 8.1 (the round cap) and section 8.2 (the verdict
  courier) are consumer guidance only.
- **Protocol `1.3`** (Workflow 2.10.0). New work items are no longer created
  while the config's default is `2.1`. Row `1` now matches only a default that
  is not retired (`2.2`); the new row `1b` reports `blocked` for a `2.1`
  default instead; `verify` gains the advisory `default_workflow_version`
  check. A `1.2` consumer meets a `blocked` decision with an unknown reason
  code and a `verify` check id it does not know: it stops, it does not misact.
  `2.1` stays in `supported_governing_versions`: existing `2.1` items run to
  completion unchanged.
- **The pinned `review_content_id` label** (`v2.6.0-002`). Review feedback
  states its content id as `Reviewed review_content_id: <64 hex>`, required
  in every two-stage stage verdict and optional elsewhere. The legacy
  alias `Reviewed review content ID:` and the bare `review_content_id:`
  still parse, and more than one distinct value parses as absent. The id
  is read from the header block only: the lines before the first `## `
  heading that follows a field line. A verdict that stated its id only
  after the first `## ` was accepted by 2.6.0 and parses as absent from
  2.7.0 on. It is the only field 2.7.0 reads more strictly than 2.6.0;
  `Status:`, `Reviewer role:` and the three binding fields keep the
  whole-file scan.
- **Durable consumed plan-review history** (`v2.6.0-001`). Every
  plan-stage `review_content_id` ever consumed (withdrawn, revised or
  amended away) is kept in the work item's
  `consumed_plan_review_content_ids`, and such content can re-enter review
  only after an edit: restoring the exact bytes is refused, and any edit
  is enough. A 2.6.0 single-slot record migrates at read time; content
  consumed before the upgrade and then displaced is the one residual. The
  state stays readable by 2.6.0, which ignores the list.
- **A two-stage `REVISE` is applied by content.** `/apply-plan-review` and
  `/apply-implementation-review` accept a two-stage `REVISE` that states
  the reviewed `review_content_id`, with its bundle fields advisory, after
  checking the on-disk bundle against its own manifest (which must name
  this work item and stage). A stated id that is not the reviewed content
  refuses, even when the bundle fields name the current bundle. `1` and
  `2.1` implementation rounds, legacy markers and verdicts that state no
  `review_content_id` keep 2.6.0's bundle binding. No command or remedy
  rewrites a reviewer's binding fields.
- **2.6.0's query CLIs** (`workflow_state.py
  --plan-review-publication-status`, `--resolve-feedback-path`) are kept
  byte-compatible; the protocol does not replace them.
- **Protocol `1.1` and Workflow 2.8.0: the default switches the gates to
  automatic.** Updating a repository to 2.8.0 with no
  `docs/ai-workflow/GATE_POLICY.json` makes its gates automatic, unless the
  file sets `"human_approval": true`, which is the one-line way back to human
  gates. The update changes no state file by itself. For a repository with
  no policy file, `next-action` changes as follows: at
  `AWAITING_PLAN_APPROVAL` (governing versions 2.1 and 2.2) it emits
  `plan.satisfy` (`validation`) or a `blocked` explanation (`14b`) instead of
  the human plan gate; at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` (2.2) the
  same for `implementation.satisfy` (`28a`, `28b`); at
  `AWAITING_FUNCTIONAL_REVIEW` it emits the evidence gates
  (`functional.evidence.external`, `pr.review.external`), `acceptance.satisfy`
  or a `blocked` explanation instead of the human gate. Items governed by `1`
  keep a human plan and technical gate, and a 2.1 item a human technical gate,
  because there is no review ledger to read. The satisfying commands write a
  `POLICY_SATISFIED` approval or an `acceptance_satisfaction` record in place of
  a person's confirmation. `GATE_POLICY.md` is the operator guide.
- **Two further consequences of the default.** The reviewer commands and
  `record-external-result` write a `Reviewer model:` line and a
  `reviewer_model` ledger key for an automatic gate that requires
  `distinct_reviewer_models` (on by default), and a manual `APPROVE` ingest that
  states no family, or the family of the other stage, is refused while its
  stage is open. A 2.7.0 item in flight whose ledger was recorded earlier can
  block at `14b` or `28b` on both `review_evidence_audited` and
  `distinct_reviewer_models`; the remedies are in `GATE_POLICY.md`.
- **With every gate human, nothing differs from protocol `1.0` except**: the
  protocol version and `workflow_release` fields; `functional_evidence` and
  `pr_review_result` are no longer refused by `record-external-result`; `verify`
  gains the advisory `gate_policy` check; `describe` lists the new
  capabilities; and, only once a pull-request fact is reported or queried, row
  `38d` emits `pr.apply_review` instead of row 39, whatever the gate modes. A
  human acceptance with `requires_pr_approved` set also queries GitHub in
  `/accept-milestone` and can emit `38g` before row 39.
- **An unaware `1.0` consumer** meets unknown action ids, which it treats as
  `blocked` (obligation 3), and the `validation` disposition, a known value that
  obligation 5 keeps it from running: a stalled item, not a wrong action.
- **Downgrade.** 2.7.0 refuses a state that carries the approval basis
  `POLICY_SATISFIED`, and reads a state that holds none. It does not check the
  optional fields 2.8.0 added (`gate_evidence`, `reopenings`,
  `acceptance_satisfaction`, the ledger audit keys, `gate_policy_adoption`,
  `gate_policy_floor`): it accepts them without acting on them. Because the
  default is automatic, such fields and bases appear in ordinary operation, so a
  downgrade is supported only for a repository that has written none.
- **A policy toggle on an older declaration.** A declaration that predates the
  exclusion of the `docs/ai-workflow/` prefix leaves a new
  `GATE_POLICY.json` (and the installed `GATE_POLICY.md` itself) unclassified for an in-flight item; the remedy depends on
  whether the stage's bundle exists and is in `GATE_POLICY.md`.

## 11. Gate policy and reopening

Protocol `1.1` reports a gate policy; it does not define one. The policy, its
evidence and its commands are specified in `GATE_POLICY.md`, which is the
full text. This section states what a consumer of the protocol relies on.

- **Rows and actions.** The catalogue (section 6.4) carries the gate-policy
  rows `14a`, `14b`, `28a`, `28b` and `38d` to `38i`; the action table
  (section 6.1) carries `plan.satisfy`, `implementation.satisfy`,
  `acceptance.satisfy` (disposition `validation`, role `validator`),
  `pr.apply_review` (automatic, role `applier`) and the external gates
  `functional.evidence.external` and `pr.review.external`. The edge table of
  section 7 carries each action's forward edge and its same-phase edge, and
  `pr.apply_review`'s edges at `MILESTONE_COMPLETE`. A decision on a gate-policy
  row carries an optional `policy` object (`source`, `digest`, `gate`, `mode`,
  and `gate_lowering` while the newest adoption lowered a gate); a row that
  existed in `1.0` never carries it.
- **Reopening.** A reopened item is the same work item, and a reopen does not
  set `active_work_item_id`: a consumer names the item (`next-action
  --work-item ID`, `/apply-pr-review ID`). A reported pull-request fact
  (`pr_review_result`) never decides a state, a reopening or a completion: it can
  only make the Workflow run its own fixed GitHub query, and `pr.apply_review`
  runs it first.
- **Trust boundary** (design decision D-GP-Trust). CI and pull-request facts that satisfy a gate come from
  GitHub, queried by the Workflow itself with one fixed `gh` invocation; an
  orchestrator's forge fact only tightens, and a gate that cannot decide
  blocks. Review verdicts and functional evidence are trusted from the
  orchestrator that reports them. The Workflow guarantees binding, freshness and
  audit, and does not guarantee provenance for them: every automatic
  satisfaction records the verdict hash, the bundle and content ids and the run
  reference, for a person to check afterwards. **Turning human approval on is the
  stronger mode.**
- **Threat model** (design decision D-GP-ThreatModel). These guarantees hold against an agent acting through the
  Workflow's commands and protocol. They do not hold against an agent that
  deliberately forges commits, trailers or state by hand, or replaces a system
  program such as `gh`; that is the same limit `/approve-review` and
  `/accept-milestone` have, and no signed commit or GitHub-side adoption is
  provided. The safeguards are the resolution rule for `gh` (an absolute path,
  refused inside the repository, a worktree, the temporary directory or a
  world-writable directory, with its path and sha256 recorded), the
  gate-lowering event reported by `verify`, `next-action` and the audit record,
  and human approval. `GATE_POLICY.md` states the threat model in full, and the
  safety rule (a policy file only ever tightens; `/adopt-gate-policy` is the one
  way to loosen).
- **No CI-produced evidence.** The CI outcome is the `checks` of the Workflow's
  own pull-request fact. Functional or review evidence produced in CI is not
  accepted, and no policy option offers it.
