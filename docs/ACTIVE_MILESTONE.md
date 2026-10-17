# Active Milestone

## In progress: CU1 (`workflow-controller-cleanup-1`)

Plan revision 3 is approved (`4d29850`); implementation is under way, one checkpoint per invocation.
- **CP1 complete** (#55, #59, #64): the release-tree docstring and the development guide state which tests read the
  installed `scripts/`; `tools/check_docs.py` rejects a roadmap step id on the task pages (`STEP_ID_RE`,
  `STEP_ID_PAGES`; the guides are exempt) and the ids it found were reworded; `docs/guide/runtime.md` explains the
  before-and-after telemetry figures. Checked with `tests.test_docs`, `tests.test_workflow_releases` and
  `tools/check_docs.py`.

The previous milestone's record follows.

## Status

**Complete.** `workflow-controller-review-modes` (`docs/ROADMAP.md` step C14, section 11.14) reached
`MILESTONE_COMPLETE` on 2026-10-17. The owner now chooses how the external review runs: the setting `review.mode`
(`auto`, `cross-model`, `single-provider`, `manual`) or `--review-mode` on `step`, `run` and `explain` for one
command. Under a gate policy that lowers the external review, `auto` runs a fresh same-provider (Claude) reviewer
(`reviewer.same_provider_model`, `reviewer.same_provider_effort`) inside a checked boundary, so a user with one
provider is never blocked at an external gate. A mode that cannot run stops at the new `review_mode_unavailable` gate
(exit 10) with the ways out. `explain` shows the mode and the choice; `telemetry --by review_mode` groups by mode. It
merged as pull request #70 (squash `2804656`), which released the minor release **1.13.0** (release notes
`docs/releases/1.13.0.md`).

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 5 was approved by policy at `4def744` after one Codex plan round (`APPROVE`, the first round). The
  base commit is `40f9cef`.
- Technical approval `18a1036` (implementation revision 3) was recorded by policy and is `CURRENT`: review content
  id `09d09207`, reviewed implementation head `68f7dc3`. Revision 2 was approved at `3cd336b` after two Codex
  implementation rounds (`REVISE` with I1-I4, then `APPROVE`); Codex round 3 approved revision 3 after the bounded
  documentation fix.
- Functional review round 1 (an independent Codex tester with fake reviewers only) passed nine of eleven flows and
  raised two Optional documentation findings (flow 1's command, flow 11's limit description), fixed by
  `d98c388`/`68f7dc3`. Round 2 passed every flow (flow-1 to flow-11), checked against evidence commit `9ba2f70`.
- The Workflow's own GitHub query found pull request #70 at head `9ba2f70` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its eleven functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C14 complete. The full milestone narrative is
archived at `docs/milestones/completed/workflow-controller-review-modes.md`, with the review history (including the
D9 operator pre-step and two later probes) and every minor review finding's disposition; the functional reports are
`.ai-review/archive/workflow-controller-review-modes/functional-tester-round1-RESULTS.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of fifteen Optional review items, ten were fixed, two dropped and three kept as follow-ups; two
  of the fixed ones also keep a follow-up, so there are five, GitHub issues #71-#75 (label `review-follow-up`; #72 also `bug`), worked off in CU1.
- **Known limitations kept out of scope:** the lowered-policy signal in the scenario is patched in (a real lowering
  adoption needs a real Workflow policy repository); a real Claude review consumes account usage. The two Git 2.56
  trailer tests (`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated; the
  `plan_stage_decisions` golden differs on the base commit as well.

## Next action

`/milestone-plan` for CU1, the clean-up milestone (`docs/ROADMAP.md`, "Next, in order"): it works through the GitHub
issues labelled `review-follow-up` and small known defects labelled `bug`, now #55-#59, #62-#67 and #71-#75. After it
come C10, C12, C11 and the orchestrator series O1-O7.
