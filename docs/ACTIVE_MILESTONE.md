# Active Milestone

## Status

**Complete.** `workflow-controller-test-leftover-lifetime` (`docs/ROADMAP.md` step C12a, follow-up 8)
reached `MILESTONE_COMPLETE` on 2026-10-12. A fake worker whose test run was killed now ends itself and its
group 300 s after its parent dies. Every other leftover a killed test run can leave (detached helpers,
daemons, sleepers, child Controllers) is bounded to 600 s or ends with its test, and
`process_fixtures.LEFTOVER_INVENTORY` records each one. The 2026-10-09 incident was replayed by hand. A
review worker killed mid-suite therefore no longer leaves a real job draining for hours. `controller/` is
unchanged. The Controller recognising leftovers it cannot tell apart from the worker's own work stays in
C12.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`). Functional review round 1
(implementation revision 3), checked against evidence commit `8dcbcb7`, passed every flow (F1-F7). An
independent tester on `anthropic/claude-sonnet-5-5` ran the flows.
- Plan revision 11 was approved by policy at `92ef06e`. The base commit is `e2064d3`.
- Technical approval `56ce0e7` was recorded by policy and is `CURRENT`: review content id `fed27aeb`,
  reviewed implementation head `b39354d`.
- The Workflow's own GitHub query found pull request #40 at head `8dcbcb7` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP4) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, including its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C12a complete. The full milestone
narrative is archived verbatim at `docs/milestones/completed/workflow-controller-test-leftover-lifetime.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge.** Pull request #40, titled `test: end the fake worker a killed test run leaves behind`, is
  merged once every check passes; a `test:` pull request releases nothing.
- **Four Minor notes from functional review round 1** (no change needed; see
  `.ai-review/archive/workflow-controller-test-leftover-lifetime/functional-review-round1-PASSED.md`). The
  checklist's wording for F1 and F4 was loose: `check_docs.py` prints nothing on success, F4's `pgrep`
  also matches the observing shell, and the leftover that lasts about 90 s is the stdin anchor, not the
  fake. Also, `tools/run_tests.py` writes its results under `/tmp/workflow-controller-tests/` even with
  scratch XDG directories, which is a possible later follow-up.

## Next action

1. Pull request #40 merges (a `test:` pull request, releasing nothing).
2. `/milestone-plan` for C5, the next step in `docs/ROADMAP.md`'s order (C5, C6, C7, C10, C12, C11, then
   O1-O7).
