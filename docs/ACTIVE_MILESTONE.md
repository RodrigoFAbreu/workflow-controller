# Active Milestone

## Status

**In progress.** `workflow-controller-review-convergence` (`docs/ROADMAP.md` step C13, section 11.13), plan revision 6
(`docs/ai-workflow/CONTROLLER_REVIEW_CONVERGENCE_PLAN.md`), base commit `45e9325`, phase `IMPLEMENTING`.

| Checkpoint | State |
| --- | --- |
| CP1 round count and cap setting | COMPLETE: `controller/review_rounds.py` (`current_round`, `cap_decision`), the `review.round_cap` setting (table generation 7), `--review-round-cap` on `step` and `run` forwarded to `job.execute_step`. Tests: `tests/test_review_rounds.py`, `tests/test_settings.py`, `tests/test_cli.py`. The real-reconciliation fixtures the plan lists for CP1 need the cap gate and are written with CP2's lifecycle scenario. |
| CP2 cap gate | pending |
| CP3 telemetry rounds | pending |
| CP4 documentation | pending |

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
