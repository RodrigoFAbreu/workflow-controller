# Active Milestone

## Status

**Complete.** `workflow-controller-installed-release-test-agnostic` (`docs/ROADMAP.md` step C9d,
follow-up 16) reached `MILESTONE_COMPLETE` on 2026-10-10. `InstalledReleaseTest` in
`tests/test_workflow_releases.py` now checks this repository's own installed Workflow through the
non-Manager steps of `managed_repo.inspect` (the installation record, the `RELEASE_CONTRACTS` arm, the
profile check) and against the `sha256` and `executable` flag of every file in
`.workflow-manager/installation.json`, with no vendored tree and no version literal.
`InstalledReleaseRefusalTest` shows a modified, missing, extra and mode-changed file, a release without
the protocol script, a protocol-major-2 `describe` and an unsupported profile each refused.
`docs/guide/development.md` says the installed Workflow needs no vendored tree. Nothing in `controller/`
changed. A later Workflow release that speaks protocol major 1 needs no test change.

The milestone was accepted **by gate policy, not by a person**: `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`), after functional review
round 1 (implementation revision 1), checked against evidence commit `294d3a8`, came back clean (flows 1-6
passed).
- Plan revision 2 was approved by policy at `fe249ce`. The base commit is `5532cdd`.
- Technical approval `bb4fa81` was recorded by policy and is `CURRENT`: bundle `05979f51`, review content
  id `bb49d2bf`, reviewed implementation head `7a13f7d`.
- The Workflow's own GitHub query found pull request #34 at head `294d3a8` with every check passing and no
  standing objection.

All registry checkpoints (CP1, CP2) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, including its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C9d and follow-up 16 complete. The full
milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-installed-release-test-agnostic.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge.** Pull request #34, titled
  `test: check the installed Workflow by capability and installation record, not by a vendored tree`, is
  merged by the Controller once every check passes; a `test:` pull request releases nothing.
- **Optional notes from the Codex implementation review:** the module docstring at
  `tests/test_workflow_releases.py:7-8` still mentions the vendored tree; `docs/guide/development.md`
  could also mention the installed protocol's `describe`.
- **Optional notes from functional review round 1** (the checklist's wording, see
  `.ai-review/archive/workflow-controller-installed-release-test-agnostic/functional-review-round1-CLEAN.md`):
  flow 2 said the whole module "may fail" without the vendored 2.9.0 tree, but it passes (29 tests OK), so
  a future checklist should expect it to pass; flow 3 case b (a missing file) fails with one failure and
  one error, because the admission test also errors on the missing file.

## Next action

1. The Controller merges pull request #34 (a `test:` pull request, no release).
2. A `docs:` roadmap pull request: the orchestrator series O1-O7 after C11, automated functional review,
   self-recovery, and configurable C8 budgets.
3. `/milestone-plan` for C8.
