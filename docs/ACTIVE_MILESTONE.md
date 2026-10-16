# Active Milestone

## Status

**Complete.** `workflow-controller-review-convergence` (`docs/ROADMAP.md` step C13, section 11.13) reached
`MILESTONE_COMPLETE` on 2026-10-16. A review loop now stops at a round cap per review stage: the setting
`review.round_cap` (default 4, 1 to 100) or `--review-round-cap` on `step` and `run` for one command. At the cap the
Controller launches no worker and stops with the `review_round_cap_reached` gate (exit 10), which names the stage,
the round, the cap, where the round came from and the three ways on. The round is the one Workflow 2.12.0 gives, or
is counted from the job records for an older Workflow (a lower bound). `telemetry` reports action, stage and round
per job (`--by stage,round` and others), so review usage can be compared before and after. It is released as the
minor release **1.12.0** when its `feat:` pull request (#61) merges.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 6 was approved by policy at `10cab7d` after three Codex plan rounds. The base commit is `45e9325`.
- Technical approval `b70be14` (implementation revision 3) was recorded by policy and is `CURRENT`: review content
  id `0b92d55a`, reviewed implementation head `b3ac08c`.
- Functional review round 1 (an independent Codex tester) passed nine of eleven flows. Flows 3 and 11 failed on one
  Optional finding: the cap gate did not name a round given by the Workflow. The bounded fix `24e66a3` fixed it, and
  Codex implementation round 3 approved revision 3. Round 2 passed every flow (flow-1 to flow-11), checked against
  evidence commit `3a21c44`.
- The Workflow's own GitHub query found pull request #61 at head `3a21c44` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP4) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its eleven functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C13 complete. The full milestone narrative is
archived at `docs/milestones/completed/workflow-controller-review-convergence.md`, with the review history and every
minor review finding's disposition; the functional reports are
`.ai-review/archive/workflow-controller-review-convergence/functional-tester-round1-RESULTS.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of eleven Optional review items, four were fixed, one dropped and six are follow-ups
  in GitHub issues #63-#67 (label `review-follow-up`), worked off in the clean-up milestone after C14.
- **Known limitations kept out of scope:** a plan round counted from job records is a lower bound; `explain` shows
  the setting's cap, not a flag's; there is no "off" value (a long loop is a cap of 100); the cap does not stop the
  Controller's own external-review courier or any non-apply action. The two Git 2.56 trailer tests
  (`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated; the `plan_stage_decisions`
  golden differs on the base commit as well.

## Next action

`/milestone-plan` for C14 (review modes, `docs/ROADMAP.md` section 11.14), the next step in `docs/ROADMAP.md`'s
"Next, in order" (C14, then a clean-up milestone for the GitHub issues labelled `review-follow-up`, then C10, C12,
C11 and the orchestrator series O1-O7).
