# Active Milestone

## Status

**Complete.** `workflow-controller-codex-reviewer` (`docs/ROADMAP.md` step C7, the smallest slice of section 5)
reached `MILESTONE_COMPLETE` on 2026-10-15. The Controller can now run the cross-model external review itself. With
`reviewer.harness` set to `codex`, a run that reaches a manual external review gate starts a review job: a Codex
reviewer works in a disposable copy behind an enforced launch boundary, passes the usage gate, and its verdict is
ingested through the Workflow. With `reviewer.harness` unset, the gate stops as before. ADR
`docs/adr/0013-review-only-harness-seam.md` records the design and the Workflow side's answer on workflow#28 (*yes,
with five conditions*). The `codex` harness is registered since the operator recorded the D12 pre-step on
2026-10-15. The milestone ships as the minor release **1.10.0** when its `feat:` pull request merges.

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
- **Merge and release.** Pull request #45, titled `feat: run the cross-model review with a Codex reviewer the
  Controller starts`, is merged once every check passes; `main.yml` then releases 1.10.0.
  `docs/release-history.md` and `docs/releases/1.10.0.md` are written after the release.
- **workflow#28 stays open** until its protocol paragraph ships in W8 (Workflow 2.12.0).
- **Known limitations kept out of scope:** only Codex is registered (other harnesses are refused); a reviewer
  may spawn a sub-agent, which the job record names with its tokens; the two Git 2.56 trailer tests
  (`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated; the
  `plan_stage_decisions` golden differs on the base commit as well.

## Next action

1. Pull request #45 merges (a `feat:` pull request, releasing 1.10.0).
2. `/milestone-plan` for C10, the next step in `docs/ROADMAP.md`'s order (C10, C12, C11, then O1-O7).
