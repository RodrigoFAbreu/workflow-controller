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

Implementation revision 3, technical approval `b70be14`. Base commit `45e9325`. Findings go to
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

**Functional review round 1.** An independent Codex tester passed flows 1, 2 and 4-10 and failed flows 3 and 11 on one
Optional finding: the cap gate named the round's source only for a round counted from job records. Fixed by a bounded
fix: the gate now says "(given by the Workflow)" for a Workflow-given round, with an assertion in
`ReviewCapGateTest` and the matching wording in `docs/common-problems.md`. Nothing was deferred to a remediation
child. The fresh implementation review is done (revision 3). **Round 2:** re-test flows 3 and 11 (the gate text for a
Workflow-given round; the matching `docs/common-problems.md` wording).

**Known limitations / out of scope.** A plan round counted from job records is a lower bound. `explain` shows the
setting's cap, not a flag's. There is no "off" value (a long loop is a cap of 100). The cap does not stop the
Controller's own external-review courier or any non-apply action. Two Git 2.56 trailer tests (`test_release_notes`,
`test_pull_request_lifecycle`) fail locally and are unrelated (follow-up 12); the `plan_stage_decisions` golden differs on
the base commit too.

**Next steps.** When testing is clean, `/accept-milestone` is the only acceptance command and needs every checkpoint in
this item's registry `COMPLETE` (CP1-CP4 are). Findings go through `/apply-functional-review` (bounded fix, or a
`-remediation-<n>` child for wider scope).

## Acceptance

Accepted **by gate policy, not by a person**, on 2026-10-16: `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`). Every checkpoint (CP1-CP4) is `COMPLETE`, the
technical approval `b70be14` (implementation revision 3) is `CURRENT`, flows flow-1 to flow-11 passed, and the
Workflow's own GitHub query found pull request #61 at head `3a21c44` with every check passing and no standing
objection. The pull request title is `feat:`, so it releases 1.12.0 when it merges.

## Review history

- **Plan.** Revision 6 was approved by policy at `10cab7d` after three Codex plan rounds (revisions 2 and 5
  `REVISE`, revision 6 `APPROVE`) plus one retry of the revision 2 round after a verdict header mismatch, with a
  local review before each Codex round.
- **Implementation.** Technical approval `c1e1c1a` (revision 2) after two Codex implementation rounds (revision 1
  `REVISE`, revision 2 `APPROVE`). After the bounded functional fix `24e66a3`, technical approval `b70be14`
  (revision 3, Codex round 3 `APPROVE`).
- **Functional.** Round 1, an independent Codex tester, passed 9 of 11 flows; flows 3 and 11 failed on one Optional
  finding (the cap gate did not name a Workflow-given round), fixed by `24e66a3`. Report:
  `.ai-review/archive/workflow-controller-review-convergence/functional-tester-round1-RESULTS.md`. Round 2 re-tested
  flows 3 and 11 and passed (`functional-review-round2-PASSED.md`). Checklist evidence commit `3a21c44`.

## Minor review findings

Every Optional finding of the milestone's reviews, with its disposition (owner rule, 2026-10-16: acceptance never
waits for a fix). The sources are the six local `APPROVE` verdicts in
`.ai-review/archive/workflow-controller-review-convergence/local/` and functional review round 1; the Codex verdicts
had no Optional findings. Follow-ups are GitHub issues labelled `review-follow-up` and are worked off in clean-up
milestones.

| # | Source | Finding | Disposition | Justification |
|---|---|---|---|---|
| 1 | Local plan review, rev 2, O1 | The fallback count missed a round opened by `pr.apply_review` (red-PR fix) | Fixed | Later plan revisions count every rise of `implementation_revision`, whichever job came between, not apply jobs: `controller/review_rounds.py:130-139`, test `tests/test_review_rounds.py:59` |
| 2 | Local plan review, rev 2, O2 | A withdrawal raises the plan round with no apply job | Fixed | Same change: the plan count reads revisions seen at a ready phase, not apply jobs. A republication with no revision rise is the documented lower bound (`controller/review_rounds.py:15-18`, `docs/guide/runtime.md:295-297`) |
| 3 | Local plan review, rev 5, O1 | Say what `round` is for `functional` and `none` jobs | Fixed | Only plan and implementation jobs get a round (`controller/telemetry.py:342`); a functional apply has no round (`tests/test_telemetry.py:389`) and a legacy job is `none` (`:323`) |
| 4 | Local plan review, rev 6, O1 | Pin the copied list of plan-review ready phases with a test | Follow-up ([#66](https://github.com/RodrigoFAbreu/workflow-controller/issues/66)) | The list is copied into `controller/review_rounds.py:65-71` with a comment, but no test pins it. Only the separate copy in `controller/evidence.py` is pinned |
| 5 | Local plan review, rev 6, O2 | On a `"1"`-governed item the plan round is always 1 | Follow-up ([#67](https://github.com/RodrigoFAbreu/workflow-controller/issues/67)) | Still true. Workflow 2.12.0 also returns no plan round for such an item, so the plan cap acts only at cap 1. Not in the docs |
| 6 | Local implementation review, rev 1, #1 | No test runs the cap gate on a real Workflow 2.12.0 answer | Follow-up ([#65](https://github.com/RodrigoFAbreu/workflow-controller/issues/65)) | The lifecycle tests still use the vendored 2.7.0 Workflow (`tests/test_review_round_lifecycle.py:4`); `tests/workflow_releases/` has no 2.12.0 |
| 7 | Local implementation review, rev 1, #2 | The before-and-after `--since` steps in the runtime guide are loose | Follow-up ([#64](https://github.com/RodrigoFAbreu/workflow-controller/issues/64)) | `docs/guide/runtime.md:305-307` is unchanged: `--since` is only a lower bound, so the "before" figures are the first query minus the second |
| 8 | Local implementation reviews, rev 2 O1 and rev 3 #1 (merged) | Working out telemetry rounds takes time that grows with the square of the job count | Follow-up ([#63](https://github.com/RodrigoFAbreu/workflow-controller/issues/63)) | Still quadratic (`controller/telemetry.py:336-348`). Not wrong, and 0.22 s on 1745 records today (rev 3's figure) |
| 9 | Local implementation review, rev 2, O2 | Docs do not say that a review the Controller runs itself always shows a `~` round | Follow-up ([#64](https://github.com/RodrigoFAbreu/workflow-controller/issues/64)) | Still true. `docs/guide/runtime.md:313-316` does not say it; the behaviour is tested (`tests/test_telemetry.py:354-368`) |
| 10 | Local implementation review, rev 3, #2 | The test-results file has no revision 3 section | Dropped | That file is a gitignored review-bundle file for a round already approved. The rev-3 reviews recorded their own independent test runs. Nothing in the repository to fix |
| 11 | Functional review, round 1, #1 | The cap gate did not name a round given by the Workflow | Fixed | 24e66a3: `controller/job.py:5876-5877` adds "(given by the Workflow)", `tests/test_protocol_job.py:420-421` checks it, and `docs/common-problems.md:92` matches |
