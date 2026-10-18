# Active Milestone

## Status

**Complete.** `workflow-controller-gate-policy` (`docs/ROADMAP.md` step C10a, the first of the three gate-policy
steps) reached `MILESTONE_COMPLETE` on 2026-10-17. The Controller now launches the Workflow's `/satisfy-gate` for the
three automatic approvals (rows 14a, 28a and 38h, on the new `satisfy-gate` role), so a gate the policy satisfies
passes without a person, and a refused validation stops the run with its unmet requirement in `explain`. A value a
protocol 1.x minor release adds is advisory or blocked, never refused (`protocol_schema.ENUM_RULES`, follow-up 17),
and a stop the Workflow calls blocked sends `lane_blocked`, not `user_gate` (part of follow-up 26). Its `feat:` pull
request (#81) merged on 2026-10-17 (squash `8a38a85`), and it is released as the minor release **1.14.0**, the latest
release. The release notes are `docs/releases/1.14.0.md`. This repository has since moved to Workflow 2.13.0 (a
`chore:` pull request, #89, 2026-10-18).

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 5 was approved by policy at `1729f63` after two Codex plan rounds (`REVISE`, then `APPROVE`); the
  plan stage reached the round cap of 4, and the owner raised the cap to 6 for C10a only. The base commit is
  `07d0d6d`.
- Technical approval `cfc8b55` (implementation revision 6) was recorded by policy and is `CURRENT`, after four Codex
  implementation rounds (three `REVISE` rounds on the lifecycle scenario and on how a refused validation's unmet
  requirement reaches `explain`, then `APPROVE` at the raised cap of 6).
- Functional review round 1 (an independent Codex tester with fakes only) passed flows 1-5 and 7 with no findings;
  flows 6, 8 and 9 were blocked by its sandbox. Round 2, an operator re-test outside the sandbox, passed them (the
  whole suite: 4,207 tests, exact coverage, only the two known Git 2.56 trailer tests and the load flake #82
  failing). Checklist evidence commit `396e18d`.
- The Workflow's own GitHub query found pull request #81 at head `396e18d` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its nine functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C10a complete. The full milestone narrative is
archived at `docs/milestones/completed/workflow-controller-gate-policy.md`, with the checkpoints, the functional
checklist, the review history and every minor review finding's disposition; the functional reports are
`.ai-review/archive/workflow-controller-gate-policy/functional-tester-round1-RESULTS.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of seventeen Optional review items, eight were fixed, four dropped and five kept as
  follow-ups, GitHub issues #83-#87 (label `review-follow-up`; #84 also `bug`), worked off in CU2.
- **Defect found while running:** #82 (label `bug`), a test that flakes with exit -14 under load; test only, for CU2.
  The functional review raised no findings.
- **Known limitations kept out of scope:** rows 38d-38g under an automatic gate still stop with `user_gate`; C10b
  (the pull request defect loop) and C10c (the automated functional review) own them. A real `/satisfy-gate` worker
  and a real GitHub query were not exercised by the flows. The two Git 2.56 trailer tests (`test_release_notes`,
  `test_pull_request_lifecycle`) fail locally and are unrelated; the bare 2.5.1 `plan_stage_decisions` golden differs
  on the base commit as well.

## Next action

`/milestone-plan` for C10b, the pull request defect loop (`docs/ROADMAP.md`, "Next, in order"). After it come C10c,
C12, C11 and the orchestrator series O1-O7; CU2, the next clean-up milestone (#78, #79 and #82-#87 so far), slots in
every few milestones or sooner when the issue count grows.
