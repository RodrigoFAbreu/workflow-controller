# Active Milestone

## Status

**Complete.** `workflow-controller-workflow-2-9-0-move` (`docs/ROADMAP.md` step C9c, follow-up 15)
reached `MILESTONE_COMPLETE` on 2026-10-09. The Workflow 2.9.0 release tree is vendored under
`tests/workflow_releases/2.9.0/` from the published archive (`archive-sha256:0f0af156…905a`), and the
sync tool vendors the gate-policy scripts (`scripts/workflow_forge.py`, `scripts/workflow_gate_policy.py`)
when a release ships them. `InstalledReleaseTest` and the live-repository admission test admit a
legacy-validated release or a protocol release by capability, and nothing else; every vendored protocol
tree (2.7.0, 2.9.0) is shown to run. Nothing in `controller/` changed. A real Workflow Manager update of
a scratch clone to 2.9.0 passes the complete CI selection with these tests and fails
`InstalledReleaseTest` without them.

The user accepted the milestone after functional review round 1 (implementation revision 1), which was
checked against evidence commit `cd5961740532df3ee7501b7d617698c27521005a` and came back clean.
- Plan revision 3 was approved at `71103fc`. The base commit is `e6ff30b`.
- Technical approval `2baf0cf` is `CURRENT`. It is an `EXTERNAL_APPROVE` of bundle `6d4441c3`, with
  review content id `3350fd5a` and reviewed implementation head `e2ccfd5`.
- External plan review round 2 asked for revisions (revision 3); both implementation-review stages
  approved revision 1 at the first round.

All registry checkpoints are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth
record of this transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C9c
and follow-up 15 complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-workflow-2-9-0-move.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge.** The milestone branch goes on a pull request titled
  `test: vendor the Workflow 2.9.0 tree and admit a protocol release by capability in the installed-release test`.
  Once every check passes, it is squash-merged; a `test:` pull request releases nothing.
- **Optional note from functional review round 1:** a checklist should expect
  `tools/workflow_releases.py check` to exit 0 with no output; it does not list the releases.

## Next action

The `chore:` pull request that moves this repository to Workflow 2.9.0
(`workflow-manager --release-version 2.9.0 update .` on a fresh branch from `main`, with no work item
active, D3). It also adds the roadmap entries the user decided on 2026-10-09. Then `/milestone-plan`
for the next incomplete milestone in `docs/ROADMAP.md`.
