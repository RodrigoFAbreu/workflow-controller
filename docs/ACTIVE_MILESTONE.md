# Active Milestone

## Status

**In progress.** `workflow-controller-usage-limit-mid-job` (`docs/ROADMAP.md` step C8b): wait out a usage limit
reached mid-job and repeat the step. Plan revision 5 is approved (`f401c6f`); the plan is
`docs/ai-workflow/CONTROLLER_USAGE_LIMIT_MID_JOB_PLAN.md`. Checkpoint state is in
`docs/ai-workflow/WORKFLOW_STATE.json`.

- CP5 complete: `docs/exit-codes.md` (rows 17, 30, 40), `docs/common-problems.md` (the mid-job entry and a pointer
  from the exit-17 entry; no heading renamed), `docs/run.md`, `docs/guide/runtime.md`, `docs/guide/workers.md` ("How a
  usage limit is recognised"), `docs/guide/development.md` and `docs/ROADMAP.md` (C8b complete, D9 follow-up
  fixed, "Next" starts at C13) agree with the behaviour. `tools/check_docs.py` and three of four golden `--check`
  forms pass; `generate_plan_stage_decisions.py --check` fails with this milestone's changes stashed too.
- CP1 complete: `controller/worker_stream.py` records the `usage_limit` fact (rejected `rate_limit_event`, 429
  error `result`, finite `resetsAt`) in the stream diagnosis, with reason `usage_limit` above `exit_status`.

- CP2 complete: `controller/job.py` classifies a limited `FAILURE` worker (`usage_limit_of`): `INTERRUPTED` with
  `usage_limit` and `UsageLimitReached` when the phase, `HEAD` and the tracked-change baseline
  (`gitrepo.tracked_change_digests`, the new `tracked_changes` pre-state member) are unchanged, in protocol mode,
  legacy mode and `resume`; any other limited job is `FAILED` with `usage_limit` in its evidence. A package-order
  fix (`usage` before `worker_stream`) and the `no_policy_lifecycle` golden were updated with it.

- CP3 complete: `controller/cli.py` waits out a limit reached mid-job. `run` builds a `mid_job` `UsageHold` at the
  fact's reset, waits through `_usage_wait` (no lock, `usage_paused`, bounded by `usage.max_wait_seconds`, with or
  without `usage.enabled`), repeats the step without counting it and stops after three limited attempts in a row;
  both stops print their own line, never "nothing was started". `step` exits 17 with a line naming the reset, an
  `INTERRUPTED` job without the fact exits 40 (it used to raise `KeyError`), `resume` names the reset, and Ctrl-C
  in the mid-job wait says the worker ran. `tests/test_usage_limit_mid_job.py` and the lifecycle scenario
  `scenario-usage-limit-mid-job-waits-and-repeats` cover it.

- CP4 complete: `controller/usage.py` `complete` measures the first job after a five-hour reset (the C8 review
  follow-up, D9). When the baseline had already expired at reservation creation and the stream's first reading is
  in a later window, that reading replaces the baseline `start` and `start_resets`, so the delta is `measured`
  (`p1 - p0`) and yields a forecast sample; it was `after_reset`, charged `max(after, reserved_five)`, no sample
  (reproduced on the unchanged code). A job admitted before the reset keeps `after_reset`. No schema change; live
  and replay (job-record block) are pinned in `tests/test_usage.py`.

- Self-review (`SELF_REVIEWING_IMPLEMENTATION`): no correctness defect found in the full diff. Two fixes: the
  `tracked_changes` member of the two early `_capture_pre_state` returns was mis-indented, and
  `tests/test_protocol_outcome.py` now covers the two CP2 cases the plan names and that were missing: index-only
  and mode-only progress also reconciled through `resume` (`FAILED`), and a limited worker whose transition
  verified (`FAILED` with the limit named, not `FINISHED`, D4).
- Full verification: `tools/check_docs.py` exit 0; golden `--check` 3 of 4 current (`plan_stage_decisions`
  differs on the base commit as well); `tools/run_tests.py` ran 4006 tests, coverage exact, with 2 failures, both
  the Git 2.56 trailer-rule tests (follow-up 12), unrelated to this milestone.

The previous milestone's record follows.

---

## Status

**Complete.** `workflow-controller-codex-reviewer` (`docs/ROADMAP.md` step C7, the smallest slice of section 5)
reached `MILESTONE_COMPLETE` on 2026-10-15. The Controller can now run the cross-model external review itself. With
`reviewer.harness` set to `codex`, a run that reaches a manual external review gate starts a review job: a Codex
reviewer works in a disposable copy behind an enforced launch boundary, passes the usage gate, and its verdict is
ingested through the Workflow. With `reviewer.harness` unset, the gate stops as before. ADR
`docs/adr/0013-review-only-harness-seam.md` records the design and the Workflow side's answer on workflow#28 (*yes,
with five conditions*). The `codex` harness is registered since the operator recorded the D12 pre-step on
2026-10-15. It merged as pull request #45 (squash `6aa8f66`), which releases the minor release **1.10.0**
(release notes `docs/releases/1.10.0.md`).

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`). Functional review round 1 (an
independent Codex tester on `openai/gpt-6.1-sol`, plus one real Codex review by the operator) passed flows 1-9 and
found two Optional documentation issues in flow 10, fixed by a bounded fix and re-tested in round 2. Round 3 (CI on
pull request #45) found one Important defect: 53 new tests assumed the git-ignored `build/` directory existed. The
bounded fix `2a427e1` creates it. Round 4 (implementation revision 9), checked against evidence commit `265e48b`,
passed every flow (flow-1 to flow-10).
- Plan revision 15 was approved by policy at `3d304d3`. The base commit is `dea2125`.
- Technical approval `8f447fc` was recorded by policy and is `CURRENT`: review content id `fa4ee49c`,
  reviewed implementation head `771496e`.
- The Workflow's own GitHub query found pull request #45 at head `265e48b` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, including its ten `functional_evidence` records and its
`acceptance_satisfaction` evidence, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C7
complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-codex-reviewer.md`.

Deferred follow-ups, not conditions of acceptance:
- **workflow#28 stays open** until its protocol paragraph ships in W8 (Workflow 2.12.0).
- **Known limitations kept out of scope:** only Codex is registered (other harnesses are refused); a reviewer
  may spawn a sub-agent, which the job record names with its tokens; the two Git 2.56 trailer tests
  (`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated; the
  `plan_stage_decisions` golden differs on the base commit as well.

## Next action

`/milestone-plan` for C8b (a usage limit reached mid-job, `docs/ROADMAP.md` section 11.6.1), the next step in
`docs/ROADMAP.md`'s order (C8b, then C13 once Workflow 2.12.0 is out, C14, C10, C12, C11, then O1-O7).
