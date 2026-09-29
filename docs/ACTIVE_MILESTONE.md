# Active Milestone

## Status

**Complete.** `workflow-controller-squash-merge-tag-versioning` (`docs/ROADMAP.md` step C1,
section 11.1) reached `MILESTONE_COMPLETE` on 2026-09-29. It makes squash merges the only merge
method, takes the release bump from the pull request's Conventional Commit title, makes the Git
tag the only version authority, and teaches close-out a squash merge (`MERGED_SQUASHED`). The user
accepted it in functional review round 1 (implementation revision 2), against checklist evidence
commit `454ffe7d6bdc9570bdcdbc1f1bf14b376bff8f31` (checklist blob
`b5357336b343966b7999562aed7936f723d18524`).
- Implementation revision 1: the local review approved it. The external Codex review asked for
  two Important fixes: an override hid a failed Git read of a historical policy in `_range_bump`,
  and a plan title declaration that was not valid UTF-8 was decoded with replacement characters.
  Both were fixed (`5802bbf`, `669d900`).
- Implementation revision 2 was approved by both implementation-review stages (technical approval
  `b82d717`, `EXTERNAL_APPROVE`, `CURRENT`).

All seven registry checkpoints (`CP1`-`CP7`) are `COMPLETE`, and the registry declares no
completion obligations. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this
transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 11.1 and step
C1 of "At a glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-squash-merge-tag-versioning.md`. It covers the goal,
checkpoint progress and the round-1 functional-review checklist.

This milestone's own deliverables remain live in the tree, unmoved (the archive file's own preface
says why):
- `docs/ai-workflow/CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md` and its registry/mapping
  files, still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry declares;
- ADR `docs/adr/0007-tag-derived-versions-and-squash-merges.md`;
- `controller/conventional_commit.py`, and the squash and tag-version changes in
  `controller/milestone_branch.py`, `controller/release_txn.py`, `controller/repo_policy.py` and
  `controller/version.py`;
- `.github/workflows/pr-title.yml`, `tools/release.py` and `tools/ci_workflows.py`;
- `docs/releases/1.4.0.md` and the updated guides, including `docs/guide/ci-and-releases.md`.

`.workflow-controller/policy.json` and `pyproject.toml`'s version line are unchanged from the base
`455cef0` (I7). This repository still runs the legacy `version_change` model until the cutover
below. The previously completed work items and their archived narratives in
`docs/milestones/completed/` are unaffected.

Deferred follow-ups, not conditions of acceptance:
- **The cutover (plan Design H, steps 1-6).** Each step is done by the user, or by the
  orchestrator only with the user's explicit authorisation:
  1. merge PR #8 with **"Create a merge commit"**. `main.yml` classifies it `NO_CHANGE`, so
     nothing is released;
  2. 1.3.0 closes the milestone out (`MERGED` → `CLOSED`);
  3. change the settings: squash merging only, with the "Pull request title and description"
     squash message; in the "Main Protection" ruleset, the squash merge method, the required
     check `PR title`, and "Require linear history";
  4. open the two-file cutover pull request (`pyproject.toml` gets `dynamic = ["version"]`, plus
     the post-cutover `.workflow-controller/policy.json` from the plan), titled
     `feat: squash merges with release versions derived from pull request titles`, and
     squash-merge it;
  5. `main.yml` classifies `RELEASE_DUE` 1.4.0 from `v1.3.0` and publishes it;
  6. install 1.4.0 once `status` shows `active: none`. Until then, 1.3.0 refuses lifecycle
     commands on this repository, because it cannot read the new policy.
- **Install timing.** A new Controller release is installed only between Workflow Manager
  milestones (shared lane plan). While a Manager milestone is running, step 6 waits, and so does
  any Controller milestone after step 4.
- **Squash code paths on GitHub.** A real squash merge happens first at step 4. Until then the
  squash title, readiness and close-out paths are covered by the suite only (checklist flow H).
- Two self-review observations are left as they are (the external review agreed): trailing
  whitespace in a declared title fails visibly on read-back, and `tag_version` counts abandoned
  tags (the only one, `v1.1.0`, is below `v1.3.0`).
- **Zombie child processes.** In long runs the Controller leaks zombie processes: orphans from
  Git's detached maintenance in test repositories. This is not this milestone's code. It is worked
  around by one step per Controller process. The user decided on 2026-09-29 that a small
  zombie-reaping milestone comes next, followed by a patch release. That milestone goes into
  `docs/ROADMAP.md` after C1 merges.
- Carried over, unchanged: `docs/ROADMAP.md` section 1.4's four follow-up patches (step C3), and
  the deferred items of the earlier milestones, listed in their acceptance commits.

The milestone branch is on Draft PR #8. The pushed head is `31ef276`. The PR is not merged. The
technical-approval commit `b82d717`, the checklist commit `454ffe7` and this acceptance commit are
local only.

**Next action:** do the cutover above. Then run `/milestone-plan` for the next milestone: the
zombie-reaping milestone the user put first, once it is in `docs/ROADMAP.md`, or otherwise step C2
of "At a glance", "CI reliability" (section 11.2), the next incomplete roadmap step. After the
cutover it is planned from `main`'s tip, with that base passed explicitly
(`/milestone-plan <main tip>`). `/milestone-plan` creates a fresh `work_items` entry and claims
`active_work_item_id`, ready for `PLANNING`.
