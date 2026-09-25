# Active Milestone

## Status

**Complete.** `workflow-controller-trunk-branch-pr-release-orchestration` reached
`MILESTONE_COMPLETE` on 2026-09-25. Functional review passed in round 1 with no findings, against
checklist evidence commit `0701930781291746c97057505f3a245f0357c118` (checklist blob
`eaea0c122ca4235a41dad2288bb7cf0c65126016`). All ten committed flows passed on a fresh, isolated
pipx install of a wheel built from that commit (`workflow-controller 1.1.1`, `runtime: package
(local build from 070193078129)`). Four extra checks also passed on fresh disposable targets:
the `checks_failing` gate (and its recovery to `merge_pull_request` once the checks turn green),
the `post_acceptance_commits` gate (persistent, PR left a draft, and the "merge anyway" exit
converging to `CLOSED`), `milestone-binding --abandon` after `PR_CLOSED_UNMERGED` was durably
recorded (`ABANDONED`, both dispositions then refused, `switch_to_trunk`, then `/milestone-plan`
from `main`), and history-rewrite detection for both a squash and a rebase merge
(`merge_method_rewrote_history` shown once, `MERGED_REWRITTEN`, no automatic switch).

All ten registry checkpoints (`CP1`-`CP10`) are `COMPLETE`. Implementation revision 2 was approved
by both implementation-review stages (technical approval `f218377`), and the user accepted the
milestone through `/accept-milestone`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth
record of this transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks
section 1.5 complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-trunk-branch-pr-release-orchestration.md`. It
covers the goal, baseline, checkpoint progress, implementation review round 1 and the
functional-review checklist, including its driver.

This milestone's own deliverables remain live in the tree, unmoved (see the archive file's own
preface for why): `docs/ai-workflow/CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md` and its
registry/mapping files, still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry
declares. The previously completed work items and their archived narratives in
`docs/milestones/completed/` are unaffected.

Supervised rollout, still outstanding: nothing of this milestone has been pushed, and
`main.yml`, the step-scoped credential helper, the real `gh` and the real release flow have only
run against fakes. The first automatic release, 1.2.0, follows the README's "Runbook: the first
automatic release" and is treated as supervised rollout evidence.

Deferred follow-ups, not conditions of acceptance:
- checklist wording: flow 5 says the guarded worker argv "ends `--disallowedTools
  Agent,Workflow,Skill,...`". That is true of the `/review-plan` worker only; the
  `/milestone-implement` worker carries the same Git/`gh` guard without the subagent tools, by
  design (`controller/routing.py`: only review and final self-review roles are single-agent)
  (functional review, observation 1);
- `status` lists every live binding record, terminal ones included (self-review observation 1);
- `repo_policy` keeps its own committed-tree Git read path (implementation review round 1,
  Optional 1), and `tests/test_packaged_runtime.py` reuses helpers from two other test modules;
- integrating a moved trunk into a milestone branch remains the manual `integration_required`
  procedure under Workflow 2.5.1; `docs/ROADMAP.md` section 1.6 revisits it against the released
  Workflow 2.6.x;
- carried over, unchanged: `docs/ROADMAP.md` section 1.4's follow-up patches (the misordered
  `--work-item` resume hints, the manual-external gate's behaviour when the local review ledger is
  incoherent, the abandoned/unreconcilable apply-review relaunch-bound tests, active-job/status
  presentation) and the other deferred items of
  `workflow-controller-release-runtime-observability`, listed in the completion status of its
  acceptance commit `82fa6a8`.

**Next action:** `docs/ROADMAP.md` section 1.4, "Follow-up patches to fold in where appropriate",
is the next incomplete milestone that can be planned now. Run `/milestone-plan` for it. That
creates a fresh `work_items` entry and claims `active_work_item_id`, ready for `PLANNING`.
Section 1.6, "Post-Workflow-2.6 compatibility integration", waits for Workflow Manager's 2.6.x
release; plan it once that release is installed through Workflow Manager. Independently of
planning, the supervised 1.2.0 release above is an operator task, not a milestone.
