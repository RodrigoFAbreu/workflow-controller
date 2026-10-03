# Active Milestone

## Status

**Complete.** `workflow-controller-orchestration-protocol-v1` (`docs/ROADMAP.md` step C9, section 1.7)
reached `MILESTONE_COMPLETE` on 2026-10-03. The Controller now drives a Workflow that answers
Orchestration Protocol major 1 (Workflow 2.7.0) through the protocol: it is admitted by capability
(the installation record lists `scripts/workflow_protocol.py` and `describe` answers major 1), the
next action and gates come from `next-action` bound to a state identity checked again before a
launch, and progress comes from `reconcile`. Workflow 2.5.1 and 2.6.0 behave exactly as under 1.6.0.
It launches three things 1.6.0 never did on a protocol target (the external-review apply, the
functional-review checklist, and applying functional findings), and `AMENDING_PLAN` launches
`/milestone-plan` instead of exiting 15. The 1.7.0 release notes are in the archive's
`## Release notes` section.

The user accepted it in functional review round 1 (implementation revision 4), against checklist
evidence commit `f668606f59bd0006f3667b628133f4ff4a472e04`, with no functional findings.
- Plan revision 11 was approved at `8b29d66` (amendment 0: the artifacts declaration and the
  checkpoint anchors only). The base commit is `97b85f0`.
- Technical approval `25adda4` (`EXTERNAL_APPROVE` of bundle `6f6770bd`, review content id
  `a9429a2b`, reviewed implementation head `9919f92`) is `CURRENT`. The last fixes bound protocol
  operations to the executed bytes, kept unknown digests unknown, ordered same-second jobs and gave
  scratch test repositories their own Git identity (I1-I4).

All registry checkpoints are `COMPLETE`, and the registry's completion obligations derive `PASS`.
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this transition, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 1.7 and step C9 of "At a
glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-orchestration-protocol-v1.md`.

Deferred follow-ups, not conditions of acceptance:
- **Release 1.7.0.** The milestone branch is on Draft PR #21, titled
  `feat: drive Workflow through Orchestration Protocol v1, decisions then outcomes`. This acceptance
  commit is local only. The next Controller step pushes it and runs readiness; once every check
  passes, the PR is marked ready and merged with "Squash and merge", without editing the commit
  message. `main.yml` then classifies `RELEASE_DUE` 1.7.0 and publishes it, and the next Controller
  step closes the milestone out.
- **Install timing.** 1.7.0 goes into the shared install only between Workflow Manager milestones
  (shared lane plan). The Manager waits for C9 before installing Workflow 2.7.0 in Controller-driven
  repos.
- **The post-C9 docs pull request**: `docs/releases/1.7.0.md` from the archived narrative's
  `## Release notes` section, and the roadmap's follow-ups.
- Carried over, unchanged: the deferred items of the earlier milestones, listed in their
  acceptance commits.

**Next action:** release 1.7.0 as above, then merge the post-C9 docs pull request. After close-out,
run `/milestone-plan` for step D1 of "At a glance" (documentation reorganisation, section 11.8),
which the user added on 2026-10-03 to run while the lane waits for W2; C8 (usage budget, section
11.6) follows, then the user's order of 2026-10-02 (C5, then C6, C7, C10, C11). Plan it from `main`'s tip, with that base passed
explicitly (`/milestone-plan <main tip>`). `/milestone-plan` creates a fresh `work_items` entry and
claims `active_work_item_id`, ready for `PLANNING`.
