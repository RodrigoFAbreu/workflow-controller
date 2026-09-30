# Active Milestone

## Status

**Complete.** `workflow-controller-ci-reliability` (`docs/ROADMAP.md` step C2, section 11.2) reached
`MILESTONE_COMPLETE` on 2026-09-30. It fixes the three timing flakes CI showed and makes "Re-run
failed jobs" count:
- an owned process's command line, first read empty inside an `execve`, now fills once readable (a
  product fix, in `controller/worker.py`);
- two tests now wait for the signal they depend on in the on-spawn window;
- shard records carry their `run_attempt`, each attempt uploads its own artifacts, and
  `tests-result` takes each shard's latest attempt, lists what it superseded, and fails when a job of
  the current attempt did not succeed;
- a leaked process fails the run (`LEAKED`, D7). Nothing is retried automatically (I6).

The user accepted it in functional review round 2 (implementation revision 2), against checklist
evidence commit `24e8464e9697abb3a45f19163ad429aaef3e984f` (checklist blob
`8045d315ed03ae335d6321e798d9a1d24b412951`).
- Implementation revision 1: both implementation-review stages approved it (technical approval
  `43008bd`). Functional review round 1 passed flows A-I, including the live "Re-run failed jobs"
  probe on the throwaway PR #14 (closed unmerged, branch deleted). It found two defects in the
  `tests-result` summary text: F1, the log and replay paths were wrong in the per-attempt layout;
  F2, the "left no fresh result" line also appeared on ordinary red runs. Both were fixed as a
  bounded functional fix (`02b75f6`, `a4ab7b5`).
- Implementation revision 2 was approved by both implementation-review stages (technical approval
  `f7fae98`, `EXTERNAL_APPROVE`, `CURRENT`). Functional review round 2 ran flow J (F1 and F2) and
  re-ran flows E and G. All three passed.

All five registry checkpoints (`CP1`-`CP5`) are `COMPLETE`, and the registry declares no completion
obligations. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this transition,
and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 11.2 and step C2 of "At a
glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-ci-reliability.md`. It covers the goal, checkpoint
progress, both functional-review rounds' checklist and the 1.4.2 release notes.

This milestone's own deliverables remain live in the tree, unmoved (the archive file's own preface
says why):
- `docs/ai-workflow/CONTROLLER_CI_RELIABILITY_PLAN.md` and its registry/mapping files, still at the
  paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry declares;
- the command-line fill in `controller/worker.py`;
- attempt selection, `--upstream-result`, `--write-selected` and the `LEAKED` verdict in
  `tools/test_shards.py` and `tools/run_tests.py`, and the per-attempt artifacts in
  `tools/ci_workflows.py` and `.github/workflows/validate.yml`;
- the regressions in `tests/test_worker.py`, `tests/test_job.py`, `tests/test_resume.py`,
  `tests/test_test_shards.py`, `tests/test_run_tests.py` and `tests/test_ci_workflows.py`;
- `docs/guide/ci-and-releases.md` ("Re-running failed jobs"), `docs/guide/development.md`,
  `docs/guide/milestone-branches.md`, `docs/guide/workers.md`, and the 1.4.2 amendments to
  `docs/adr/0004-worker-lifecycle-ownership.md` and `docs/adr/0005-adaptive-test-sharding.md`.

`.workflow-controller/policy.json`, `pyproject.toml`, `setup.py` and `.github/workflows/ci.yml`,
`main.yml` and `pr-title.yml` are unchanged from the base `93b82de`.

Deferred follow-ups, not conditions of acceptance:
- **Release 1.4.2.** The milestone branch is on Draft PR #13, titled
  `fix: stop the known CI flakes and make a re-run of failed jobs count`. The pushed head is
  `24e8464`; this acceptance commit is local only. The next Controller step pushes it and runs
  readiness. Once every check passes, the PR is marked ready and merged with "Squash and merge",
  without editing the commit message. `main.yml` then classifies `RELEASE_DUE` 1.4.2 from `v1.4.1`
  and publishes it, and the next Controller step closes the milestone out (`MERGED_SQUASHED` →
  `CLOSED`).
- **Release notes.** As for 1.4.1, the notes (the archived "Pull request body" section) reach
  neither the squash commit nor the GitHub release until step C3 (11.1.2). After the release, a
  docs pull request adds `docs/releases/1.4.2.md` from that section.
- **Install timing.** 1.4.2 goes into the shared install only between Workflow Manager milestones
  (shared lane plan), since the Manager lane's Controller runs from it.
- Left as the plan scoped them: `--write-selected` inside `--results-dir` makes a later local
  `aggregate` over that directory see duplicate records and refuse (LOCAL-IMPL-R1-001, optional;
  CI writes to a sibling directory), and a 1.4.1 checkout cannot read schema-2 shard records (only
  a local `--replay` across versions is affected).
- The 2.5.1 `plan_stage_decisions` golden `--check` differs at the base too (the documented
  `AMENDING_PLAN` difference), unchanged by this milestone.
- Carried over, unchanged: the vendored Workflow conformance suites' Git-maintenance hygiene (a
  Workflow repository item), and the deferred items of the earlier milestones, listed in their
  acceptance commits.

**Next action:** release 1.4.2 as above. After close-out, run `/milestone-plan` for step C3 of "At a
glance": the settings file v1, the 1.4 cleanup patches, telemetry v0 and release notes that follow
the milestone (sections 1.4, 8 and 11.1.2). It is the next incomplete roadmap step. Plan it from
`main`'s tip, with that base passed explicitly (`/milestone-plan <main tip>`). `/milestone-plan`
creates a fresh `work_items` entry and claims `active_work_item_id`, ready for `PLANNING`.
