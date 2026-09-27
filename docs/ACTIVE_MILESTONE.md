# Active Milestone

## Status

**Complete.** `workflow-controller-adaptive-test-sharding` reached `MILESTONE_COMPLETE` on
2026-09-27. Functional review passed in round 3 (implementation revision 6) with no findings,
against checklist evidence commit `a09b6c120963bc0e95c2a88d3670bed6da8946fc` (checklist blob
`ed42abb2191d9d80815f7c242dd4388482106193`). The required re-test, L1, passed on CI run
`36330896229`, attempts 1 and 2, on head `a09b6c1`:
- every `Validate` job was green both times: `plan`, the 5 `tests` shards, `tests-result` and
  `package`;
- coverage was exact both times (2041 planned tests, each ran once);
- the critical path was 3:49 both times, within both acceptance bars (5 min, and 4 min 8 s);
- round 2's two findings did not recur: `OwnershipTest.test_a_gated_escapee_is_published_as_group_then_as_tag`
  (F1) and `RunRecordCtrlCTest.test_sigint_marks_the_run_interrupted_and_keeps_the_orphan` (F2)
  were both in the run's plan and passed on both attempts.

The other flows (local A-K, M, N and CI L2-L4) passed in round 2 and were not repeated; the round-3
head added only round 2's two test-only fixes.

All ten registry checkpoints (`CP1`-`CP5`, `CP5B`, `CP5C`, `CP6`, `CP6B`, `CP7`) are `COMPLETE`.
Implementation revision 6 was approved by both implementation-review stages (technical approval
`a5d9095`, `CURRENT`), and the user accepted the milestone through `/accept-milestone`.
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this transition, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 1.2.1 complete. The full
milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-adaptive-test-sharding.md`. It covers the goal, the
three plan amendments, checkpoint progress, functional review rounds 1 and 2, and the round-3
functional-review checklist.

This milestone's own deliverables remain live in the tree, unmoved (see the archive file's own
preface for why): `docs/ai-workflow/CONTROLLER_ADAPTIVE_TEST_SHARDING_PLAN.md` and its
registry/mapping files, still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry
declares, ADR `docs/adr/0005-adaptive-test-sharding.md`, `tools/test_shards.py`,
`tools/run_tests.py`, `tools/test_timings.json` and the rendered `validate.yml`. The previously
completed work items and their archived narratives in `docs/milestones/completed/` are unaffected.

Deferred follow-ups, not conditions of acceptance:
- the round-6 local implementation review's optional findings: `CrossProcessEventSeqTest` may
  have the same SIGKILL-before-`worker_spawned` window round 2's F2 closed, and a matched-wakeup
  assertion (0.9 s) can flake under heavy load. Neither failed in the round-3 CI runs;
- the round-3 checklist's flow N quoted the round-2 CI plan digest (`8850f631...`); the CI plan
  on the accepted head is `7240aaf5...`, still 5 shards. The digest changed only because F1 added
  one test;
- what the plan leaves for later: failing a run on a leaked process (D7, today a warning), and
  periodic refreshes of the committed CI timing profile (a refresh changes a protected path, so
  it goes through review);
- making the drain detach bound (10800 s, amendment 1) configurable, listed under
  `docs/ROADMAP.md` section 1.4;
- the two Controller behaviour changes (`source` follows the current ownership basis; the 10800 s
  detach bound) go in the next release's notes, per `docs/ROADMAP.md` section 1.2.1;
- carried over, unchanged: `docs/ROADMAP.md` section 1.4's four follow-up patches, and the
  deferred items of the earlier milestones, listed in their acceptance commits.

The milestone branch is on Draft PR #1 (head `a09b6c1`, pushed for functional-review CI evidence
with the user's authorization) and is not merged; this acceptance commit is local only.

**Next action:** `docs/ROADMAP.md` section 1.4, "Follow-up patches to fold in where appropriate",
is the next incomplete milestone that can be planned now. Its four patches are still open. Run
`/milestone-plan` for it. That creates a fresh `work_items` entry and claims
`active_work_item_id`, ready for `PLANNING`. Section 1.6, "Post-Workflow-2.6 compatibility
integration", waits for Workflow Manager's 2.6.x release; plan it once that release is installed
through Workflow Manager.
