# Active Milestone

## In progress

**`workflow-controller-test-leftover-lifetime`** (`docs/ROADMAP.md` step C12a, follow-up 8; plan
`docs/ai-workflow/CONTROLLER_TEST_LEFTOVER_LIFETIME_PLAN.md`, revision 11, base `e2064d3`). A fake worker (and the
other processes tests start) whose test run was killed now ends by itself, so a review worker that is killed
mid-suite no longer leaves a real job draining for hours.

| checkpoint | state |
| --- | --- |
| CP1 orphan watchdog, readiness file, contract tests, readiness-gated loss helpers | complete (verified: `tests.test_fake_claude_contract`, `test_worker`, `test_resume`, `test_lifecycle_orchestration`, 420 tests OK) |
| CP2 detached helpers, daemons and sleepers bounded; inventory | complete (verified: `tests.test_fake_claude_contract`, `test_worker`, `test_job`, `test_lock`, `test_handoff`, `test_run_tests`, `test_resume`, `test_lifecycle_orchestration`, `test_cli`, `test_observe`, `test_test_shards` OK; the audit table is `process_fixtures.LEFTOVER_INVENTORY`, checked by `OrphanWatchdogTest`) |
| CP3 child Controllers end with their test; incident replays | complete (verified: `tests.test_fake_claude_contract`, `test_worker`, `test_resume`, `test_lifecycle_orchestration`, `test_observation_equivalence`, `test_packaged_runtime`, `test_trunk_preflight`, `test_cli`, `test_handoff`, 654 tests OK, 1 skipped; the three D7 replays by hand: fake gone in 2.9-3.4 s of its orphaning, child Controller in 0.9 s, anchor in 65-68 s) |
| CP4 documentation and roadmap | pending |

The section below describes the previous, completed milestone.

## Status

**Complete.** `workflow-controller-usage-budget` (`docs/ROADMAP.md` step C8, section 11.6) reached
`MILESTONE_COMPLETE` on 2026-10-10. The Controller reads the Claude and Codex usage windows itself,
forecasts a job from telemetry, admits it atomically against the readings and the other lanes'
reservations in one shared usage record, and pauses a run before a limit (exit 17) instead of starting a
job that would not fit. `workflow-controller usage` gives a manually started worker the same gate
(`--check`, `--wait`, `--reserve`, `--renew`, `--release`). Settings table generation 4 adds the sixteen
`usage.*` rows; `run --usage-cap` and `step --usage-cap` cap a run. The design is recorded in
`docs/adr/0011-usage-budget.md`.

The milestone was accepted **by gate policy, not by a person**: `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`), after functional review
round 2 (implementation revision 15), checked against evidence commit `123b1d3`, passed every flow
(F0-F9).
- Plan revision 11 was approved by policy at `03ddec0`. The base commit is `f516e76`.
- Technical approval `bcdebf8` was recorded by policy and is `CURRENT`: bundle `0d3e6701`, review content
  id `94edf9b8`, reviewed implementation head `a9f6e93`.
- The Workflow's own GitHub query found pull request #36 at head `123b1d3` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, including its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C8 complete. The full milestone
narrative is archived verbatim at `docs/milestones/completed/workflow-controller-usage-budget.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge and release.** Pull request #36, titled
  `feat: a usage budget that pauses a run before a Claude or Codex limit`, is merged once every check
  passes; a `feat:` pull request releases 1.8.0.
- **Two Minor wording findings from functional review round 2** (user decision, 2026-10-10: they go into
  the 1.8.0 release-notes `docs:` pull request, not into C8; see
  `.ai-review/archive/workflow-controller-usage-budget/functional-review-round2-PASSED.md`):
  `docs/common-problems.md` and `docs/guide/runtime.md` should say that a damaged record must be restored
  before the next `step`, `run` or `resume`, because after one the pending job is marked `done` without a
  charge; and the checklist's F4/F7 wording missed `usage-events.jsonl` and the exit 20 of a further
  `run`.
- **Accepted optional follow-ups** (see the archived checklist's "Known limitations"): a usage-limit result
  inside a worker stream is C8b; per-repository caps and per-lane budgets are C8c; the accumulator totals at
  the absolute numeric limit; the second unreadable-record warning when `resume` re-attaches to a running
  job.

## Next action

1. Pull request #36 merges (a `feat:` pull request, releasing 1.8.0).
2. The 1.8.0 release-notes `docs:` pull request, with the two Minor wording findings above.
3. `/milestone-plan` for C5, the next step in `docs/ROADMAP.md`'s order (C5, C6, C7, C10, C12, C11, then
   O1-O7).
