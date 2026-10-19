# Active Milestone

## Status

**Complete.** `workflow-controller-round-caps` (C13b, `docs/ROADMAP.md`; controller issue
[#88](https://github.com/RodrigoFAbreu/workflow-controller/issues/88)) reached `MILESTONE_COMPLETE` on 2026-10-19. It
caps local and external review rounds separately (`review.local_round_cap` 6, `review.external_round_cap` 4, on the
Workflow's split counts of protocol 1.5); `review.round_cap` and `--review-round-cap` keep working as deprecated
aliases, retired after 1.17.0. Its pull request is #107 (title `feat:`, so it releases the minor release 1.17.0 once
merged).

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 3 was approved by policy at `513c873`. The base commit is `d6b9dfc`.
- Technical approval `6e08dd8` (implementation revision 1) was recorded by policy and is `CURRENT`.
- Functional review (an independent Codex tester and an operator re-run) passed all ten flows. Checklist evidence
  commit `7449d1e`.
- The Workflow's own GitHub query found pull request #107 at head `7449d1e` with every check passing and no standing
  objection.

All registry checkpoints (CP1-CP4) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition. `docs/ROADMAP.md` marks step C13b complete. The full narrative is archived at
`docs/milestones/completed/workflow-controller-round-caps.md`; the Optional findings are in
`docs/milestones/completed/workflow-controller-round-caps-findings.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of ten Optional items, eight were fixed and two kept as a follow-up, GitHub issue #108
  (label `review-follow-up`: `settings show` prints the stored, not the effective, caps).
- **Follow-up 59:** retire `review.round_cap` and `--review-round-cap` after 1.17.0.
- **Follow-up 60:** vendor Workflow 2.15.0 so the lifecycle tests exercise the split counts end to end.

## Next action

The next incomplete milestone in `docs/ROADMAP.md` is **A2** (the backlog), which waits for
[workflow#48](https://github.com/RodrigoFAbreu/workflow/issues/48) to be released; **C10c** follows. After the owner
merges pull request #107 (squash) and 1.17.0 is released, run `/milestone-plan` for the next milestone.
