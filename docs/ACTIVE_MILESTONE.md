# Active Milestone

## Status

**Complete.** `workflow-controller-workflow-2-6-integration` reached `MILESTONE_COMPLETE` on
2026-09-28. It admits Workflow 2.6.0 beside 2.5.1 by exact release and ships as Controller 1.3.0.
The user accepted it in functional review round 2 (implementation revision 6), against checklist
evidence commit `7ec4939c0671ad1f044724f94eafad3933a170d5` (checklist blob
`fd6d3c79aabe31e82216db263869d56476bbf225`).
- Round 1 (checklist `eeeb4fa`) passed every flow except I: PR #5's CI was red. The cause was F1,
  a test-only defect: `GitIsolationTest` depended on the runner's system Git configuration, where
  a `git-lfs` filter is set.
- The bounded fix (`ff5a5cd`, `81b50af`) isolated those tests from the host's Git configuration,
  added an ambient-filter regression, and reworded one sentence of the 1.3.0 notes. It changed no
  production code.
- CI was green again at `56fdba4` and at `0938a87`. Round 2 re-tested I, H and J; A-G were
  unaffected.

All six registry checkpoints (`CP1`-`CP6`) are `COMPLETE`, and every completion obligation in the
registry derived `PASS`. Both implementation-review stages approved revision 6 (technical approval
`930e1bc`, `CURRENT`), and the user accepted the milestone through `/accept-milestone`.
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this transition, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 1.6 and step 1 of "At a
glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-workflow-2-6-integration.md`. It covers the goal,
the functional-review checklist, checkpoint progress and implementation review rounds 1-4.

This milestone's own deliverables remain live in the tree, unmoved (the archive file's own preface
says why):
- `docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md` and its registry/mapping files,
  still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry declares;
- ADR `docs/adr/0006-workflow-release-admission-and-per-release-contracts.md`;
- `controller/workflow_contract.py`, the vendored release trees and `tools/workflow_releases.py`;
- `docs/releases/1.3.0.md` and `pyproject.toml` at `1.3.0`.

The previously completed work items and their archived narratives in `docs/milestones/completed/`
are unaffected.

Deferred follow-ups, not conditions of acceptance:
- **This repository's own update to Workflow 2.6.0** (plan Decision 2). It is a separate change,
  made after the 1.3.0 PR is merged, the release is published and 1.3.0 is installed, with no
  active work item. The procedure is in the plan's Decision 2 and in `docs/guide/installation.md`:
  a `chore/workflow-2.6.0` branch, the Manager's `update` and `verify`, a full test run, then a PR
  with only the Manager's output. CP6's dry run showed it needs no Controller change and
  classifies `NO_CHANGE`.
- **Binding `gitrepo.merge_trunk`.** E1-E5 found no integration transition in the released 2.6.0
  contract, so `integration_required` and the documented manual merge stay. ADR 0006 names the
  narrow Workflow follow-up that would let a later milestone wire it.
- **Known limitations, documented, not tested as working.** These are refused or unsupported:
  - a target whose tracked files select a filter driver, such as Git LFS, is refused for 2.6.0
    queries (`query_git_not_isolated`);
  - updating a target while a plan-approval journal is in flight;
  - re-planning at `IMPLEMENTING` under 2.6.0.

  Two over-refusals are documented in troubleshooting and fail closed: a `hook.<name>.event`
  disabled by `hook.<name>.enabled = false`, and an executable `post-index-change` at the
  worktree's root with an empty `core.hooksPath`.
- **No live-worker end-to-end flow on 2.6.0.** It would cost real worker spend. Flows F and G
  cover it with the real Workflow scripts.
- **CI time.** In CP6's dry run, the 2.6.0 acceptance-matrix suite took 179.3 s, against 80.5 s
  for 2.5.1. Watch the critical path once this repository runs 2.6.0.
- **Round-1 functional observation.** The CI failure went unnoticed through two external review
  rounds, because the review prompt said CI was green. Future review rounds should read the PR's
  checks first.
- Carried over, unchanged: `docs/ROADMAP.md` section 1.4's four follow-up patches (step 3 of "At a
  glance"), and the deferred items of the earlier milestones, listed in their acceptance commits.

The milestone branch is on Draft PR #5. The pushed head is `0938a87`, pushed for
functional-review CI evidence. The PR is not merged. The technical-approval commit `930e1bc`, the
checklist commit `7ec4939` and this acceptance commit are local only.

**Next action:** step 2 of `docs/ROADMAP.md`'s "At a glance", "Squash merges, with the release
version derived from pull request titles", is the next incomplete milestone. It has no numbered
roadmap section yet ("new; the first slice of automatic merging (1.8/1.9)"). Plan Decision 2 puts
two steps before it, both authorised by the user when they happen:
1. merge PR #5, so 1.3.0 is released, and install 1.3.0;
2. move this repository to Workflow 2.6.0, as in the first deferred item above.

The next milestone then runs under Workflow 2.6.0. Run `/milestone-plan` for it. That creates a
fresh `work_items` entry and claims `active_work_item_id`, ready for `PLANNING`.
