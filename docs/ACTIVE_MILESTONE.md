# Active Milestone

## Status

**Complete.** `workflow-controller-adapters-1` (A1, `docs/ROADMAP.md`) reached `MILESTONE_COMPLETE` on 2026-10-19. It
puts the harness and the git host behind adapter registries (`controller/harness.py`, `controller/forge.py` `FORGES`)
and adds the usage record (`controller/usage_record.py`, with the versioned `controller/prices.json`), written beside
`telemetry` at the end of every worker and review job. It changes no behaviour except the new `usage_record` key in
`resume --json`. Its pull request is #102 (squash `70016a4`, merged 2026-10-19), released as the patch release **1.16.1**, the latest
release. The release notes are `docs/releases/1.16.1.md`.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 5 was approved by policy at `b3d5f08`. The base commit is `859eda5`.
- Technical approval `2761ec0` (implementation revision 6) was recorded by policy and is `CURRENT`.
- Functional review (an independent Codex tester and an operator re-test) passed all nine flows. Checklist evidence
  commit `4e8d287`.
- The Workflow's own GitHub query found pull request #102 at head `4e8d287` with every check passing and no standing
  objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition. `docs/ROADMAP.md` marks step A1 complete. The full narrative is archived at
`docs/milestones/completed/workflow-controller-adapters-1.md`; the Optional findings are in
`docs/milestones/completed/workflow-controller-adapters-1-findings.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of twenty-three Optional items, thirteen were fixed, four dropped and six kept as
  follow-ups, GitHub issue #104 (label `review-follow-up`), for a later clean-up milestone.
- **#103** (fill the price table from the providers' published API list prices). #103 and #104 are planned before A3.
- **#88** (the two review round caps) waits for a Workflow release that counts local and external rounds separately
  (workflow#43).

## Next action

No milestone is active. The next incomplete step is **A2** (the backlog, `docs/ROADMAP.md`), which waits for
[workflow#48](https://github.com/RodrigoFAbreu/workflow/issues/48), the Workflow lane's next milestone after W12. In
the meantime this repository moves to Workflow 2.15.0, and then controller issue #88 is taken. If workflow#48 is not
released by then, **C10c** follows. Run `/milestone-plan` for the next milestone.
