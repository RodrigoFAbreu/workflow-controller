# Active Milestone

## Status

**Complete.** `workflow-controller-ci-time` (C2b, `docs/ROADMAP.md`) reached `MILESTONE_COMPLETE` on 2026-10-22. A pull
request's CI fell from about 85 minutes (46 after the timings refresh of 2026-10-22) to 19.3 minutes at its
acceptance head (24.3 before this milestone's own timings refresh, 20.9 after it): the whole-catalogue checks run on `main` and gate the release, the
tests run in six shards, lifecycle tests share built snapshots, and the `FollowCommandTest` flake
([#121](https://github.com/RodrigoFAbreu/workflow-controller/issues/121)) is fixed. Its pull request is #123 (merged, no release).
[#119](https://github.com/RodrigoFAbreu/workflow-controller/issues/119) is closed: the target is met.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 8 was approved by policy at `2175fb9`. The base commit is `cfe51e8`.
- Technical approval `78fcf35` (implementation revision 3) was recorded by policy and is `CURRENT`.
- Functional review (twelve flows) passed. Checklist evidence commit `492f07d`.
- The Workflow's own GitHub query found pull request #123 at head `492f07d` with every check passing and no standing
  objection.

All registry checkpoints (CP1-CP6) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition. `docs/ROADMAP.md` marks step C2b complete. The full narrative is archived at
`docs/milestones/completed/workflow-controller-ci-time.md`; the Optional findings are in
`docs/milestones/completed/workflow-controller-ci-time-findings.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of twenty-five Optional items, thirteen were fixed, four dropped with a reason and eight
  kept as follow-ups, GitHub issues #124 and #125.
- **Known limitations:** an ordinary release now waits about 32 minutes after the merge
  ([#126](https://github.com/RodrigoFAbreu/workflow-controller/issues/126)). `generate_plan_stage_decisions.py --check` fails on the untouched base too. Two Git
  2.56 trailer tests fail on `main` too and are out of scope.

## Next action

C2b is merged (PR #123). The next milestone is **C10e** (#113, a command that verifies a new `claude` build for the
functional tester), then **A2** (the backlog), then **C10d**. Run `/milestone-plan` for C10e.
