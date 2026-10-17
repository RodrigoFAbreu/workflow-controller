# Active Milestone

## Status

**Complete.** `workflow-controller-cleanup-1` (`docs/ROADMAP.md` step CU1, the first clean-up milestone) reached
`MILESTONE_COMPLETE` on 2026-10-17. It fixed the sixteen GitHub issues labelled `review-follow-up` or `bug` (#55-#59,
#62-#67 and #71-#75): documentation and `check_docs` rules, gaps in the test-leftover guard and the usage-limit
tests, two flaky worker tests, the `"1"` plan round count, a full model id for the Claude reviewer, the review's own
harness named in recovery messages, one-pass `telemetry` rounds with a Claude review's cost and tokens kept, and
Workflow 2.12.0 vendored for the tests. Its `fix:` pull request (#77) merged on 2026-10-17 (squash `f46999d`) and
closed the sixteen issues, and it is released as the patch release **1.13.1**. The release notes are
`docs/releases/1.13.1.md`.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 3 was approved by policy at `4d29850` after two Codex plan rounds (`REVISE` on the scope of the
  step-id rule, then `APPROVE`). The base commit is `f60250f`.
- Technical approval `08cffc8` (implementation revision 4) was recorded by policy and is `CURRENT`, after three
  Codex implementation rounds inside the round cap of 4 (`REVISE` with I1-I3, `REVISE`, then `APPROVE`).
- Functional review round 1 (an independent Codex tester with fakes only) passed flows 1-7 and raised one Optional
  documentation finding on flow 9, fixed by `4faea2a`; flows 8 and 10 were blocked by its sandbox. Round 2, an
  operator re-test outside the sandbox, passed flows 8-10 (the whole suite: 4,183 tests, exact coverage, only the
  two known Git 2.56 trailer tests failing). Checklist evidence commit `5e6b90a`.
- The Workflow's own GitHub query found pull request #77 at head `5e6b90a` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP6) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its ten functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step CU1 complete. The full milestone narrative is
archived at `docs/milestones/completed/workflow-controller-cleanup-1.md`, with the issues it fixes, the review
history and every minor review finding's disposition; the functional reports are
`.ai-review/archive/workflow-controller-cleanup-1/functional-tester-round1-RESULTS.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of eleven Optional review items, six were fixed, three dropped and two kept as
  follow-ups, GitHub issues #78 and #79 (label `review-follow-up`), worked off in CU2.
- **Known limitations kept out of scope:** a real Claude review consumes account usage, so the flows used fake
  reviewers only. The two Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`) fail locally
  and are unrelated; the bare 2.5.1 `plan_stage_decisions` golden differs on the base commit as well.

## Next action

`/milestone-plan` for C10, gate policy (`docs/ROADMAP.md`, "Next, in order"). After it come C12, C11 and the
orchestrator series O1-O7; CU2, the next clean-up milestone (#78 and #79 so far), slots in every few milestones or
sooner when the issue count grows.
