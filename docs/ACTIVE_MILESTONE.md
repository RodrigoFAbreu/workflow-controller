# Active Milestone

## Status

**In progress.** `workflow-controller-review-convergence` (`docs/ROADMAP.md` step C13, section 11.13), plan revision 6
(`docs/ai-workflow/CONTROLLER_REVIEW_CONVERGENCE_PLAN.md`), base commit `45e9325`, phase `AWAITING_FUNCTIONAL_REVIEW` (implementation revision 2, technical approval recorded at `c1e1c1a`).

| Checkpoint | State |
| --- | --- |
| CP1 round count and cap setting | COMPLETE: `controller/review_rounds.py` (`current_round`, `cap_decision`), the `review.round_cap` setting (table generation 7), `--review-round-cap` on `step` and `run` forwarded to `job.execute_step`. Tests: `tests/test_review_rounds.py`, `tests/test_settings.py`, `tests/test_cli.py`. The real-reconciliation fixtures the plan lists for CP1 need the cap gate and are written with CP2's lifecycle scenario. |
| CP2 cap gate | COMPLETE: `job._review_cap_gate` (after the loop guard in `_decide_protocol_current`, and in `explain` beside it with the setting's cap), `protocol_decision.REVIEW_ROUND_CAP_REACHED`, `execute_step` forwards `review_round_cap`. Tests: `ReviewCapGateTest` in `tests/test_protocol_job.py`, the explain wiring in `tests/test_protocol_decision.py`. The real-reconciliation and lifecycle-scenario fixtures the plan lists are not yet written (they need the fake Workflow answers of `tests/scenarios/lifecycle.py`); they are carried to the full-suite step. |
| CP3 telemetry rounds | COMPLETE: `telemetry.rows` adds `action`, `stage`, `round` and `round_source` per job (the round stored in the job's decision snapshot, else `review_rounds.current_round` over the unfiltered records up to and including that job, the cap gate's own function; `review_rounds.record_key` made public for the shared order), `GROUPINGS` gains `action`, `stage`, `stage,round` (a `jobs`-derived round labelled `N~`, kept apart from the Workflow's `N`) and `work_item,action`; `--by` help lists them. No job record is written. Tests: `RoundsTest` in `tests/test_telemetry.py`, which pins the text of the existing groupings and the JSON row keys. |
| CP4 documentation | COMPLETE: `docs/guide/runtime.md` (the `review.round_cap` row, the cap, the before and after procedure), `docs/guide/commands.md`, `docs/run.md`, `docs/common-problems.md`, `docs/exit-codes.md`, `docs/ROADMAP.md` (C13 complete, next C14). The full suite also needed two pins updated: the module order (`review_rounds` before `telemetry`) and `execute_step`'s parameter list. The real-reconciliation and lifecycle-scenario fixtures listed for CP1 and CP2 were written in the self-review below and the implementation-review round. |

**Self-review** (`SELF_REVIEWING_IMPLEMENTATION`). One important finding: the plan's real-reconciliation and
lifecycle-scenario tests were missing. Added:

- `tests/test_review_round_lifecycle.py`: real vendored 2.7.0 targets (no `review_round`, so the round comes from the
  job records) driven by the real `execute_step`, `next-action` and `reconcile`, with the worker's effect made by the
  target's own Workflow writers and generator. A plan loop: the first local review runs at cap 1, the apply stops
  (no worker, Workflow state and `HEAD` unchanged, one `GATE_BLOCKED` record that no count includes), a raised cap
  applies it and reaches the next review, and a republication without a `plan_revision` rise leaves the count at 2
  while the Workflow 2.12.0 `_plan_review_round` of the same state is 3 (a lower bound: the cap fires later, never
  earlier). A first publication at revision 2 after unpublished `PLANNING` authoring at 1 (the shape
  `create_remediation_child_work_item` leaves, made by routing at 1 and authoring at 2) is round 1. An
  implementation loop: an ordinary apply opens round 2, a `same_content` apply ends `progress` with the revision
  unchanged and opens none, the cap stops the apply at round 2, and a `"2.1"` apply that ends `gate_reached` opens a
  round. `telemetry` derives the same round per job as the gate, courier (Controller external-review) jobs included: they store
  `review.action_id` rather than a `protocol` block, and `telemetry.is_courier_job` reads that shape.
- `scenario-review-round-cap-stops-and-continues` (`tests/scenarios/lifecycle.py`, mutation `cap-ignored`): `cli.main`
  `run --review-round-cap 1`, `explain` under `review.round_cap` 1 (the same gate text), then
  `run --review-round-cap 2` continues to the next review and stops again.
- `ReviewCapGateTest.test_a_legacy_decision_is_not_capped`; `docs/guide/runtime.md` names the real group labels
  (`stage=plan, round=4~`) and release 1.12.0; `docs/guide/development.md` lists the new scenario.

A bounded functional fix is covered through real reconciliation too
(`FunctionalFixLoopTest` in `tests/test_review_round_lifecycle.py`): from `AWAITING_FUNCTIONAL_REVIEW`, an ordinary
republication takes `implementation_revision` 1 to 2 and the local review that follows runs at round 2; a
`same_content` one ends at revision 2 with no new round; the cap gate and `telemetry` read the same rounds.

**Verification.** `python3 tools/check_docs.py` exit 0. Golden `--check`: three current; `plan_stage_decisions.json`
(release 2.5.1) differs from a fresh derivation exactly as at the base commit `45e9325` (the known, permitted
difference), untouched here. `python3 tools/run_tests.py`: 4064 tests, coverage exact, 3 failures: the two Git 2.56
trailer-rule tests (follow-up 12, unchanged by this milestone) and the scenario-count pin (18 to 19), fixed and
re-run green.

## Functional review checklist

Implementation revision 2, technical approval `c1e1c1a`. Base commit `45e9325`. Findings go to
`.ai-review/workflow-controller-review-convergence/feedback/FUNCTIONAL_REVIEW.md`.

**Setup.** Work in a clone with a scratch bare `origin` and a fake `gh` on `PATH` (a clone's `origin` is the real
repository). Run the tool from the checkout: `PYTHONPATH=<checkout> python3 -P -m controller ...`, with
`--routing-config .controller/routing.json` before the subcommand. No feature flag or real provider is needed; the
worker is a fake `claude`. Do not run the whole test suite backgrounded with `&` (Ctrl-C tests fail spuriously).

**Test data.** Seed a managed repository whose Workflow answers `next-action` for a plan or implementation review loop
(see `tests/test_review_round_lifecycle.py` and `tests/scenarios/lifecycle.py`): one Workflow 2.12.0-shaped answer with
`review_round` set, and one older answer without it. Two or more earlier protocol job records for the same work item.

| # | Flow | Expected |
|---|------|----------|
| 1 | `settings show`, then `settings` file fill on an existing settings file. | A `review.round_cap` row: integer, default **4**, bounds 1 to 100, flag `--review-round-cap`; table generation 7; an existing file gains the row. |
| 2 | `step --review-round-cap 0`, `101`, `abc`. | argparse usage error, exit **2**, nothing launched. |
| 3 | Plan loop at cap 1: `step` on `plan.review.local` at round 1, then (after a `REVISE`) `step` on `plan.apply_review`. | The review runs. The apply stops with the `review_round_cap_reached` gate: no worker launched, Workflow state and `HEAD` unchanged, exactly one `GATE_BLOCKED` record, exit **10**. The gate text names the stage, the round, the cap, the source and the three ways on. |
| 4 | Boundary table at cap 4, both stages: apply at round 3 runs, at 4 stops; review at 4 runs, at 5 stops. | As stated. Authoring, implementing, self-review, manual-record actions and the Controller external-review courier are never stopped. |
| 5 | After flow 3, `run --review-round-cap 2` (or `6`). | The apply launches, the loop reaches the next review, then stops again at the new cap. A following plain `run` stops again (the flag applies to one command). |
| 6 | `explain` on the stopped item. | Same gate text as the step; it shows the setting's cap and says a flag is not reflected. |
| 7 | Answer without `review_round` (a 2.11.0-shaped snapshot). | The round is counted from job records, `source: jobs`; a plan stop says the Workflow's count may be higher. The cap fires later than the Workflow's own number, never earlier. A `PLANNING`-authored child first published at revision 2 is round 1. |
| 8 | Implementation loop: ordinary apply, a `same_content` apply, a `"2.1"` apply that ends `gate_reached`, and a bounded functional fix from `AWAITING_FUNCTIONAL_REVIEW`. | An ordinary apply or a bounded fix raises `implementation_revision` and opens a round; a `same_content` republication opens none; the `"2.1"` `gate_reached` apply opens one. |
| 9 | The loop guard and the cap both apply. | The loop guard takes precedence. A legacy-path target is unaffected. |
| 10 | `telemetry --by stage,round`, `--by action`, `--by stage`, `--by work_item,action`, plus `--format json` if offered. | New groupings work; a Workflow-stated round is `plan/4`, a job-derived one `plan/4~`, in separate rows; JSON rows gain only `action`, `stage`, `round`, `round_source`; existing groupings and their text are unchanged. A job's round is the same with and without `--since`. Controller external-review jobs (`plan.review.external`, `implementation.review.external`) show their action, stage and round. |
| 11 | Read `docs/guide/runtime.md`, `docs/guide/commands.md`, `docs/run.md`, `docs/common-problems.md`, `docs/exit-codes.md`, `docs/guide/development.md`, `docs/ROADMAP.md`. | They match flows 1-10; ROADMAP marks C13 complete with C14 next; `python3 tools/check_docs.py` exits 0. |

Automated evidence for these flows: `tests/test_review_rounds.py`, `tests/test_settings.py`, `tests/test_cli.py`,
`ReviewCapGateTest` in `tests/test_protocol_job.py`, `tests/test_protocol_decision.py`, `tests/test_telemetry.py`,
`tests/test_review_round_lifecycle.py` and the lifecycle scenario `scenario-review-round-cap-stops-and-continues`.

**Known limitations / out of scope.** A plan round counted from job records is a lower bound. `explain` shows the
setting's cap, not a flag's. There is no "off" value (a long loop is a cap of 100). The cap does not stop the
Controller's own external-review courier or any non-apply action. Two Git 2.56 trailer tests (`test_release_notes`,
`test_pull_request_lifecycle`) fail locally and are unrelated (follow-up 12); the `plan_stage_decisions` golden differs on
the base commit too.

**Next steps.** When testing is clean, `/accept-milestone` is the only acceptance command and needs every checkpoint in
this item's registry `COMPLETE` (CP1-CP4 are). Findings go through `/apply-functional-review` (bounded fix, or a
`-remediation-<n>` child for wider scope).

The previous milestone follows.

---

## Status

**Complete.** `workflow-controller-usage-limit-mid-job` (`docs/ROADMAP.md` step C8b, section 11.6.1) reached
`MILESTONE_COMPLETE` on 2026-10-16. A usage limit reached in the middle of a job is now recognised in the worker
stream (a rejected `rate_limit_event`, a 429 error `result` and a finite `resetsAt`). A limited job that verifiably
changed nothing ends `INTERRUPTED` with `UsageLimitReached`; `run` waits for the reset without holding the lock and
repeats the step, up to three limited attempts in a row; `step` exits 17 and names the reset. A limited job that
changed state stays `FAILED` (exit 30) with the limit in its evidence. The first job after a five-hour reset is now
measured (the C8 review follow-up). It merged as pull request #52 (squash `1033872`), which released the minor
release **1.11.0** (release notes `docs/releases/1.11.0.md`).

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 5 was approved by policy at `f401c6f`. The base commit is `988e8c3`.
- Technical approval `8d560cf` (implementation revision 8) was recorded by policy and is `CURRENT`: review content
  id `6db1d78b`, reviewed implementation head `16daa27`.
- Functional review round 1 (an independent Codex tester on `openai/gpt-6.1-sol`) passed eight flows. Flow 7 failed
  only on the checklist's wording: it listed an unmapped limit type as "not a limit", which the plan counts as one.
  That and a second Optional documentation finding (the exit-40 timeout example) were fixed by the bounded fix
  `16daa27`. Round 2 passed every flow (flow-1 to flow-9 and flow-7b), checked against evidence commit `4e2a13b`.
- The Workflow's own GitHub query found pull request #52 at head `4e2a13b` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its ten functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C8b complete. The full milestone narrative is
archived verbatim at `docs/milestones/completed/workflow-controller-usage-limit-mid-job.md`; the functional reports
are `.ai-review/archive/workflow-controller-usage-limit-mid-job/functional-review-round1.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **`explain` omits an ordinary usage limit** (O1 from the Codex implementation reviews; `docs/ROADMAP.md`
  follow-up 30): `controller/observe.py` filters out a stream diagnosis whose only reason is `usage_limit`, so
  `explain` does not show the limit for an ordinary limited failure.
- **Known limitations kept out of scope:** a limited job that changed state is not repeated (exit 30); only a
  rejected event with a 429 result and a finite reset is recognised, never text alone; the two Git 2.56 trailer
  tests (`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated; the
  `plan_stage_decisions` golden differs on the base commit as well.

## Next action

`/milestone-plan` for C13 (review convergence, `docs/ROADMAP.md` section 11.13). This repository runs Workflow
2.12.0 (W8) since 2026-10-16. C13 is the next step in `docs/ROADMAP.md`'s "Next, in order" (C13, then C14, C10, C12, C11, then O1-O7).
