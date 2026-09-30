# Active Milestone

## Status

**Complete.** `workflow-controller-child-process-reaping` (`docs/ROADMAP.md` step C1b, section
11.1.1) reached `MILESTONE_COMPLETE` on 2026-09-30. The Controller now collects every finished child
it holds as a subreaper, on every supervision tick and in every worker state, each by its own pid.
It never collects the worker, its anchor or a child the process waits for, and a background reaper
covers the gaps between launches. The tests' throwaway Git repositories no longer run automatic
maintenance. The user accepted it in functional review round 1 (implementation revision 2), against
checklist evidence commit `56aa479f9d4a1f25adb2e8c28e207389c2649868` (checklist blob
`809afca9ce7e510e284c925e0e9df34dadf64343`).
- Implementation revision 1: the local review approved it. The external Codex review asked for one
  fix: a launch's final sweep could drop another launch's exclusion for a reused pid. It was fixed
  in `be4b9f2` (`_LAUNCH_OWNERS`, owner-checked removal, with a `ReapingSweepTest` regression).
- Implementation revision 2 was approved by both implementation-review stages (technical approval
  `987017d`, `EXTERNAL_APPROVE`, `CURRENT`).
- Functional review: flows A-G passed (the unittest suite's orphans fell from about 7,080 to 87). Flow H
  passed only in part: the documentation matches, but the 1.4.1 release notes reach neither the PR
  body, the squash commit nor the GitHub release (see the follow-ups below). The user accepted with
  that as a follow-up.

All three registry checkpoints (`CP1`-`CP3`) are `COMPLETE`, and the registry declares no
completion obligations. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this
transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 11.1.1 and step
C1b of "At a glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-child-process-reaping.md`. It covers the goal,
checkpoint progress, the 1.4.1 release notes and the round-1 functional-review checklist.

This milestone's own deliverables remain live in the tree, unmoved (the archive file's own preface
says why):
- `docs/ai-workflow/CONTROLLER_CHILD_PROCESS_REAPING_PLAN.md` and its registry/mapping files, still
  at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry declares;
- the reaping changes in `controller/worker.py` and `controller/cli.py`;
- `tests/fixtures.py`'s `git_init`/`git_clone`, the guard `tests/test_fixtures_git_hygiene.py`,
  the fake worker's orphan bursts (`tests/fake_claude.py`), and the regressions in
  `tests/test_worker.py` and `tests/test_cli.py`;
- `docs/guide/workers.md` ("Collecting finished children"), `docs/guide/development.md`
  ("Throwaway Git repositories") and the 1.4.1 amendment to
  `docs/adr/0004-worker-lifecycle-ownership.md`.

`.workflow-controller/policy.json`, `pyproject.toml`, `setup.py` and `.github/` are unchanged from
the base `6a24f64`.

Deferred follow-ups, not conditions of acceptance:
- **Release 1.4.1.** The milestone branch is on Draft PR #11, titled
  `fix: reap every finished child process the Controller holds as a subreaper`. The next Controller
  step pushes this acceptance commit and runs readiness. Once every check passes, the PR is marked
  ready and merged with "Squash and merge", without editing the commit message. `main.yml` then
  classifies `RELEASE_DUE` 1.4.1 from `v1.4.0` and publishes it, and the next Controller step closes
  the milestone out (`MERGED_SQUASHED` → `CLOSED`).
- **Install timing and the stopgap.** Install 1.4.1 only between Workflow Manager milestones (shared
  lane plan). After that, both lanes drop the `--max-steps 1` stopgap from their scripts. The
  stopgap keeps working with 1.4.1 until then.
- **Release notes (functional flow H).** The squash body is fixed text, readiness overwrites the PR
  body, and the GitHub release notes are `workflow-controller {tag}`, so the 1.4.1 release notes
  (the archived "Pull request body" section) are published nowhere. The user decided on 2026-09-30:
  after 1.4.1 is released, a docs pull request adds `docs/releases/1.4.1.md` from that section,
  and a roadmap follow-up makes a milestone's release-notes section the PR body and the GitHub
  release notes.
- **Vendored Workflow conformance suites.** Their fixtures still create Git repositories with
  automatic maintenance on (about 3,490 orphans per full run). 1.4.1 collects those orphans every
  tick. The fixtures belong to the Workflow repository, as a hygiene item there.
- Left as the plan scoped them: `resume`'s supervision has no sweep, and the rate-limited full
  `/proc` fallback can leave a zombie for one ownership-scan interval on a host without
  `task/<tid>/children`.
- The 2.5.1 `plan_stage_decisions` golden `--check` differs at the base too (the documented
  `AMENDING_PLAN` difference), unchanged by this milestone.
- Carried over, unchanged: the known CI flakes (`OwnershipTest`, `CrossProcessEventSeqTest`, step
  C2), `docs/ROADMAP.md` section 1.4's four follow-up patches (step C3), and the deferred items of
  the earlier milestones, listed in their acceptance commits.

**Next action:** release 1.4.1 as above. After close-out, run `/milestone-plan` for step C2 of
"At a glance", "CI reliability" (section 11.2), the next incomplete roadmap step, planned from
`main`'s tip with that base passed explicitly (`/milestone-plan <main tip>`). `/milestone-plan`
creates a fresh `work_items` entry and claims `active_work_item_id`, ready for `PLANNING`.
