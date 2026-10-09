# Active Milestone

## In progress: `workflow-controller-workflow-2-9-0-move` (ROADMAP C9c)

Plan revision 3, base `e6ff30b`. Implementing one checkpoint per invocation.

- **CP1 complete.** `tests/workflow_releases/2.9.0/` is vendored from the published archive
  (`archive-sha256:0f0af156…905a`); the sync subset gained `GATE_POLICY_PATHS`
  (`scripts/workflow_forge.py`, `scripts/workflow_gate_policy.py`); the 2.5.1, 2.6.0 and 2.7.0
  trees are unchanged. `tools/workflow_releases.py check` exits 0 and
  `python3 -m unittest tests.test_workflow_releases` passes.

---

## Status

**Complete.** `workflow-controller-protocol-warn-status` (`docs/ROADMAP.md` step C9b, follow-up 11)
reached `MILESTONE_COMPLETE` on 2026-10-09. Controller 1.7.1 treats a Workflow `verify` check with
status `warn` as advisory. The check is not refused: it is shown as a `verify <check>: warn: <detail>`
line in every decision's evidence, and the unhealthy gate lists it after the failing checks. An
undefined status is still refused with exit 20. The Workflow 2.9.0 protocol schema (protocol 1.2)
is vendored byte for byte. `inspect` advises about the four catalogue action ids this release cannot
launch. The task and reference pages document 2.9.0 and the 1.7.0 refusal.

The user accepted the milestone after functional review round 2 (implementation revision 4), which
was checked against evidence commit `58ecff2591a1f3e40135681c396fc6085e4ebdf5` and came back clean.
- Plan revision 2 was approved at `46f0fbb`. The base commit is `8cab96d`.
- Technical approval `8a78b0a` is `CURRENT`. It is an `EXTERNAL_APPROVE` of bundle `46feeec5`, with
  review content id `1ff6add7` and reviewed implementation head `975d10b`.
- External review round 1 asked for the job path to use the shared `decide_after_preflight`
  (revision 2). Functional review round 1 found an unisolated checklist setup, stale page headers and
  wording; these were fixed as a bounded change (revisions 3 and 4) and reviewed again by both
  implementation-review stages.

All registry checkpoints are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth
record of this transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C9b
and follow-up 11 complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-protocol-warn-status.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge and release.** The milestone branch is on PR #28, titled
  `fix: accept the protocol's warn check status and vendor the Workflow 2.9.0 schema`. Once every
  check passes, the PR is squash-merged without editing the commit message, and the release
  workflow publishes 1.7.1.
- **The post-1.7.1 docs pull request.** It holds:
  - the 1.7.1 release notes (`docs/releases/1.7.1.md`);
  - the optional notes from functional review round 2: one sentence that an undefined status is
    still refused on four more pages, and a checklist setup that pushes `main` to the scratch origin;
  - "the latest compatible Workflow at the time of the move" wording;
  - a roadmap entry for workflow-controller#27 (a cross-session message fails a worker).

## Next action

1.7.1 is released (PR #28) and its docs are merged (PR #29). Next, `/milestone-plan` for C9c: move
this repository to the latest compatible Workflow (2.9.0 today), with automatic gates (ROADMAP
follow-up 15). Then C8.
