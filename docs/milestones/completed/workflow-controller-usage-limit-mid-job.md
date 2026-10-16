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

## Functional review checklist

Implementation revision 7, technical approval `df5de7b` (`CURRENT`). Base commit `988e8c3`. Findings go to
`.ai-review/workflow-controller-usage-limit-mid-job/feedback/FUNCTIONAL_REVIEW.md`.

**Setup.** Work in a clone with a scratch bare `origin` and a fake `gh` on `PATH` (a clone's `origin` is the real
repository). Run the tool from the checkout: `PYTHONPATH=<checkout> python3 -P -m controller ...`. No feature flag or
real provider is needed; the worker is a fake `claude` that prints a stream-json stream. Do not run the whole test
suite backgrounded with `&` (Ctrl-C tests fail spuriously).

**Test data.** Two fake-worker streams, each ending the job with exit 1:
- *limited*: a `rate_limit_event` with status `rejected` and a finite `resetsAt` a few seconds ahead, then a `result`
  with `is_error: true` and API status 429. Samples: `tests/golden/claude_stream_usage_limit_rejected.md`.
- *plain failure*: an error `result` with no rate-limit event.
A seeded work item at `IMPLEMENTING` (see `tests/test_lifecycle_orchestration.py`, `seed`).

| # | Flow | Expected |
|---|------|----------|
| 1 | Run the *limited* worker for one `step` when it changes nothing (no commit, no tracked edit; an untracked file is allowed). | Job `INTERRUPTED` with `usage_limit` and `UsageLimitReached`; exit **17**; stderr says "usage limit reached mid-job" and names the reset; it never says "nothing was started". |
| 2 | Same worker, but it first commits, edits a tracked file, stages a change, or changes a file mode. | Job `FAILED`, exit **30**; the record's evidence carries the limit; not repeated. A rename or copy of a tracked path also counts as a change. |
| 3 | `run` with the *limited* worker on the first attempt and a normal worker on the second, `usage.enabled` false. | `run` waits (no lock held, `usage_paused`) until the reset plus `usage.resume_grace_seconds`, repeats the step, and the repeat does not use a `--max-steps` step. Repeat with `usage.enabled` true. |
| 4 | `run` where every attempt is limited. | Stops after **three** limited attempts in a row, exit 17, line says the worker ran. |
| 5 | `run` where the reset is farther than `usage.max_wait_seconds`. | Stops at once, exit 17, line names the reset and says the worker ran. Ctrl-C during the mid-job wait says the worker ran. |
| 6 | `step` or `run` on an `INTERRUPTED` job with no limit fact (for example a protocol job whose worker timed out having changed nothing; on the legacy path an unchanged timed-out worker ends the job `FAILED`/30). | Exit **40** and "job ... was interrupted ... a new `step` repeats it"; no `KeyError`. `resume` on a limited record names the reset. |
| 7 | Streams that must NOT count as a limit: an earlier `rejected` event then a successful turn; localised text only; a missing or non-finite `resetsAt`. | No `usage_limit` fact; plain `FAILED`. A timeout or signal keeps its own reason. |
| 7b | A rejected event whose limit type is unmapped (for example `opus_only`), job changed nothing. | It IS a limit: `INTERRUPTED`, exit **17**, `window: null` and the event's own `resetsAt`. |
| 8 | First job after a five-hour reset (`controller/usage.py` `complete`): baseline expired at reservation, first reading in a later window. | Delta is `measured` (`p1 - p0`) and yields a forecast sample. A job admitted before the reset still gets `after_reset`. |
| 9 | Read `docs/exit-codes.md` (rows 17, 30, 40), `docs/common-problems.md` (the mid-job entry), `docs/run.md`, `docs/guide/workers.md`, `docs/ROADMAP.md`. | They match flows 1-8; `python3 tools/check_docs.py` exits 0. |

Automated evidence for these flows: `tests/test_worker_stream.py`, `tests/test_job.py`, `tests/test_protocol_outcome.py`,
`tests/test_gitrepo.py`, `tests/test_usage_limit_mid_job.py`, `tests/test_usage.py` and the lifecycle scenario
`scenario-usage-limit-mid-job-waits-and-repeats`.

**Known limitations / out of scope.** A limited job that had changed state is not repeated automatically (exit 30). Only
rejected-event + 429 + finite reset is recognised; text alone never is. Two Git 2.56 trailer tests
(`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated (follow-up 12); the
`plan_stage_decisions` golden differs on the base commit too.

**Next steps.** When testing is clean, `/accept-milestone` is the only acceptance command and needs every checkpoint in
this item's registry `COMPLETE`. If one is outstanding, finish it with `/milestone-implement` and return here.
Findings go through `/apply-functional-review` (bounded fix, or a `-remediation-<n>` child for wider scope).
