# Active Milestone

## Status

**Complete.** `workflow-controller-pr-defect-loop` (`docs/ROADMAP.md` step C10b, the second of the three gate-policy
steps) reached `MILESTONE_COMPLETE` on 2026-10-18. Under an automatic acceptance gate the Controller now launches the
Workflow's `/apply-pr-review` (`pr.apply_review`, on the new `apply-pr-review` role) and, at the pull-request external
gates (rows 38f and 38g), runs the Workflow's one fixed `gh pr list` query itself and hands the verbatim result to
`record-external-result --kind pr_review_result` (the `pr_report` step). Pending checks wait only when they are the
one thing awaited, and an unchanged result is not reported twice, so a pull request whose checks were pending or whose
defect was fixed reaches acceptance without a person. It is released as the minor release **1.15.0** when its `feat:`
pull request (#92) merges. No `docs/releases/1.15.0.md` was written by the milestone; the release notes come in the
docs pull request after the release.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 5 was approved by policy at `751a968` after two Codex plan rounds (`REVISE`, then `APPROVE`) and five
  local plan rounds. The base commit is `164d813`.
- Technical approval `2ee65f4` (implementation revision 4) was recorded by policy and is `CURRENT`, after four local
  and four Codex implementation rounds, one of them a disputed `REVISE` that Codex withdrew on a re-grade. The owner
  set a technical round cap of 10 for this milestone, stopping at 6 local or 4 external rounds per stage.
- Functional review round 1 (an independent Codex tester with fakes only) passed flows 1-8, 10 and 12 and found the
  pull request wait documented as a `--wait` option that does not exist, fixed by the documentation fix `15b3485`;
  flow 9 was blocked by its sandbox. Round 2, an operator re-test outside the sandbox, passed flows 9 and 11 (the
  whole suite: 4,256 tests, exact coverage, only the two known Git 2.56 trailer tests failing). Checklist evidence
  commit `0ba7ddc`.
- The Workflow's own GitHub query found pull request #92 at head `0ba7ddc` with every check passing and no standing
  objection.

All registry checkpoints (CP1-CP4) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its twelve functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C10b complete. The full milestone narrative is
archived at `docs/milestones/completed/workflow-controller-pr-defect-loop.md`, with the checkpoints, the functional
checklist, the review history and every minor review finding's disposition; the functional reports are
`.ai-review/archive/workflow-controller-pr-defect-loop/functional-tester-round1-RESULTS.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of sixteen Optional review items, seven were fixed (one of them the functional finding),
  four dropped and five kept as follow-ups, GitHub issues #93-#95 (label `review-follow-up`), worked off in CU2.
  In short: #93 matches the `ci_green:` token rather than any `ci_green` text before waiting on pending checks; #94
  tightens the tests of the `gh` vetting; #95 sets `vet_root` directly in `Context.forge`.
- **Known limitations kept out of scope:** a red pull request found after acceptance (the merge flow) is not looped;
  that is C10d. Row 38e (functional evidence) is still a stop; C10c owns it. A real `/apply-pr-review` worker and a
  real GitHub query were not exercised by the flows. The two Git 2.56 trailer tests (`test_release_notes`,
  `test_pull_request_lifecycle`) fail locally and are unrelated.

## Next action

`/milestone-plan` for C10c, the automated functional review (`docs/ROADMAP.md`, "Next, in order"). After it come C10d,
C12, C11 and the orchestrator series O1-O7; CU2, the next clean-up milestone (#78, #79, #82-#88, #91 and #93-#95 so
far), slots in every few milestones or sooner when the issue count grows.
