# Active Milestone

## Status

**Complete.** `workflow-controller-automated-functional-review` (C10c, `docs/ROADMAP.md`) reached
`MILESTONE_COMPLETE` on 2026-10-21. The Controller now runs the functional review itself at row 38e: an isolated
sandbox, an independent tester, one `functional_evidence` result per checklist flow, and findings for the applier when
a flow fails. Its pull request is #111 (title `feat:`, so it releases the minor release 1.18.0 once merged).

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 8 was approved by policy at `2480ee9`. The base commit is `1599a4d`.
- Technical approval `6eb86c3` (implementation revision 11) was recorded by policy and is `CURRENT`.
- Functional review (an independent Codex tester and an operator live run) passed all nine flows. Checklist evidence
  commit `df1acdd`.
- The Workflow's own GitHub query found pull request #111 at head `df1acdd` with every check passing and no standing
  objection.

All registry checkpoints (CP1-CP6) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition. `docs/ROADMAP.md` marks step C10c complete. The full narrative is archived at
`docs/milestones/completed/workflow-controller-automated-functional-review.md`; the Optional findings are in
`docs/milestones/completed/workflow-controller-automated-functional-review-findings.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of sixty-one Optional items, thirty-one were fixed, fifteen dropped with a reason and
  fifteen kept as follow-ups, GitHub issues #114 to #118 (label `review-follow-up`).
- **Roadmap follow-ups 61-66:** a Codex tester, `required_flows` coverage tooling, a tester-failure cap setting, a
  sandbox-side forge model, extra sandbox tools and a newer vendored Workflow.
- **Known limitations:** pull-request flows are unsupported in the sandbox; a copy edited under the tester's own
  `TMPDIR` is not detected (stated limit of D5). Two Git 2.56 trailer tests fail on `main` too and are out of scope.

## Next action

The next incomplete milestone in `docs/ROADMAP.md` is **A2** (the backlog), which waits for
[workflow#48](https://github.com/RodrigoFAbreu/workflow/issues/48) to be released; **C10d** follows C10c. After the
owner merges pull request #111 (squash) and 1.18.0 is released, run `/milestone-plan` for the next milestone.
