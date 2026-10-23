# Active Milestone

## Status

**Complete.** `workflow-controller-claude-build-verify` (C10e, `docs/ROADMAP.md`; issues
[#113](https://github.com/RodrigoFAbreu/workflow-controller/issues/113) and
[#114](https://github.com/RodrigoFAbreu/workflow-controller/issues/114)) reached `MILESTONE_COMPLETE` on 2026-10-23.
`workflow-controller verify-claude [--force]` runs the live sandbox enforcement probes against the `claude` a launch
would run and records its digest in `verified-claude-builds.json` in the runtime root only when every probe holds and
the build's bytes are unchanged by the run. It never touches the settings file. The static probe and the launch admit
the built-in `VERIFIED_NATIVE_BUILDS` plus the recorded digests, and both examine and execute one resolved real path.
Its pull request is #128 (title `feat:`), to be released as 1.19.0.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 3 was approved at `e1e10e2`. The base commit is `679b34c`.
- Technical approval `a6d1c6a` (implementation revision 5) was recorded by policy and is `CURRENT`.
- Functional review passed all nine flows (round 2, after the F1 fix). Checklist evidence commit `468e2b9`.
- The Workflow's own GitHub query found pull request #128 at head `468e2b9` with every check passing and no standing
  objection.

All registry checkpoints (CP1-CP4) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition. `docs/ROADMAP.md` marks step C10e complete. The full narrative is archived at
`docs/milestones/completed/workflow-controller-claude-build-verify.md`; the Optional findings are in
`docs/milestones/completed/workflow-controller-claude-build-verify-findings.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of twenty-nine Optional items, seven were fixed, fourteen dropped with a reason and eight
  kept as follow-ups, GitHub issues #132 and #133.
- **Known limitations:** recording a digest needs a real `claude` and a model, so the live path is covered by the owner
  (or the opt-in `CONTROLLER_LIVE_CLAUDE_TESTS=1` tests); 2.1.308 fails the live boundary probe (#129). A build file
  rewritten in place between the hash and the exec is not caught. The credential-read denial and `file://` origin
  detection (#115) and probes in CI are out of scope. Two Git 2.56 trailer tests fail on `main` too and are out of
  scope.

## Next action

Controller 1.19.0 is released (C10e, pull request #128). This repository runs Workflow 2.18.5. Next, in order (see `docs/ROADMAP.md`): `/milestone-plan` for **C10f** (#129), then #126, **CU3** (more than 8 `review-follow-up` issues are open) and **A2** (the backlog).