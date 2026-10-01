# Active Milestone

## Status

**Complete.** `workflow-controller-settings-and-telemetry` (`docs/ROADMAP.md` step C3, sections 1.4,
8 and 11.1.2) reached `MILESTONE_COMPLETE` on 2026-10-01. It adds four things, each opt-in or
behaviour-preserving by default:
- **a settings file v1** (1.4): one user-level JSON file holds every operational tunable and the
  routing defaults. The Controller fills in missing settings, moves an untouched value only forward,
  warns about unknown keys, and `settings show|path|clean` inspects and tidies it
  (`docs/adr/0008-controller-settings-file.md`);
- **telemetry v0** (8): each job records its session's totals, and the read-only `telemetry`
  command summarises them, deriving older jobs from `worker.stdout`;
- **release notes that follow the milestone** (11.1.2): when a repository opts in, readiness puts the
  milestone's notes section into the pull request body under a digest-bound marker, and the release
  publishes the verified blocks of its range or refuses and names the fix;
- **the four open 1.4 patches**: every printed resume and explain hint parses, the manual-external
  gate tells the truth about an incoherent ledger, relaunch-bound tests, and a useful `status` with
  `--json`.

The user accepted it in functional review round 2 (implementation revision 4), against checklist
evidence commit `44f7a113a770f748592d3677e221fb600ed713e4` (checklist blob
`7f18937d4b3e1df885409ae84aa24df61ab66bb8`).
- Plan revision 14 was approved at `b0e2f9a` (`EXTERNAL_APPROVE`). During planning, the user
  narrowed the release-notes design (Decision 11): notes come only from commit-message blocks in
  the release range, and narrative scanning was dropped.
- Implementation revisions 1-3: the manual external review returned `REVISE` on revision 1
  (`4d87e48`, `c5b00e7`), and the local review returned `REVISE` on revision 2 (`e38d0d5`). Both
  review stages approved revision 3 (technical approval `2683558`).
- Functional review round 1 (checklist `f04faed`) passed flows A-P and found five wording findings,
  F1-F5. They were fixed as a bounded functional fix (`4afffae`, `86f78af`, `778338b`, `862cd21`,
  `fa89897`). Both implementation-review stages approved revision 4 (technical approval `9f7a2b0`,
  `EXTERNAL_APPROVE`, `CURRENT`). Functional review round 2 re-ran the affected flows and the
  regressions, and it was clean.

All seven registry checkpoints (`CP1`-`CP7`) are `COMPLETE`, and the registry declares no completion
obligations. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this transition,
and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks sections 1.4 and 11.1.2, the
telemetry part of section 8, and step C3 of "At a glance" complete. The full milestone narrative is
archived verbatim at `docs/milestones/completed/workflow-controller-settings-and-telemetry.md`. It
covers the goal, checkpoint progress, the review-round fixes, functional review round 2's checklist
and the 1.5.0 release notes.

This milestone's own deliverables remain live in the tree, unmoved (the archive file's own preface
says why):
- `docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md` and its registry/mapping files, still
  at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry declares;
- the new modules `controller/settings.py`, `controller/telemetry.py`, `controller/worker_stream.py`
  and `controller/release_notes.py`, and the changes to `controller/cli.py`, `job.py`, `observe.py`,
  `routing.py`, `runtime.py`, `evidence.py`, `decision.py`, `milestone_branch.py`, `release_txn.py`,
  `repo_policy.py`, `gitrepo.py`, `forge.py`, `worker.py`, `workflow_contract.py` and `errors.py`,
  and `tools/release.py notes-block`;
- the new test modules `tests/test_settings.py`, `test_telemetry.py`, `test_worker_stream.py`,
  `test_release_notes.py` and `test_hints_parse.py`, the settings isolation in `tests/__init__.py`,
  the regenerated `tests/golden/no_policy_lifecycle.json` and `plan_stage_decisions.2.6.0.json`, and
  the additions to the existing test modules;
- `docs/adr/0008-controller-settings-file.md`; `docs/guide/runtime.md` ("The settings file"),
  `docs/guide/commands.md`, `docs/guide/workers.md` ("Telemetry"),
  `docs/guide/milestone-branches.md` ("Release notes in the pull request body"),
  `docs/guide/ci-and-releases.md` ("Release notes from the milestones"),
  `docs/guide/troubleshooting.md` and `docs/README.md`.

`.workflow-controller/policy.json`, `pyproject.toml`, `setup.py` and `.github/workflows/` are
unchanged from the base `a47e695`.

Deferred follow-ups, not conditions of acceptance:
- **Release 1.5.0.** The milestone branch is on Draft PR #16, titled
  `feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches`.
  The pushed head is `44f7a11`; this acceptance commit is local only. The next Controller step
  pushes it and runs readiness. Once every check passes, the PR is marked ready and merged with
  "Squash and merge", without editing the commit message. `main.yml` then classifies
  `RELEASE_DUE` 1.5.0 from `v1.4.2` and publishes it, and the next Controller step closes the
  milestone out (`MERGED_SQUASHED` → `CLOSED`).
- **Release notes, one last time by hand.** This repository has not opted in to release notes yet,
  so 1.5.0's GitHub release carries the fixed text. After the release, a docs pull request adds
  `docs/releases/1.5.0.md` from the archived narrative's `## Release notes` section. A later small
  `chore:` pull request turns release notes on in `.workflow-controller/policy.json` (the cutover
  `docs/guide/ci-and-releases.md` describes).
- **Install timing.** 1.5.0 goes into the shared install only between Workflow Manager milestones
  (shared lane plan), since the Manager lane's Controller runs from it.
- **A roadmap item for the next docs pull request**, reported by the Manager lane on 2026-10-01: in
  `AMENDING_PLAN`, `explain` and step selection exit with an error on a
  `workflow_fingerprint.UnclassifiedPathError` from the plan-review publication-status probe, so
  the Controller cannot select `/milestone-plan`, which is the step that repairs the problem. It
  should fail closed with a named reason and that resume command.
- Left as the plan scoped them, or found after acceptance and optional:
  - LIR1-O1, where an end-marker line with a trailing space or CR is refused as unattributable. The
    plan's grammar says "exactly".
  - `status` prints a Python `None` for `pinned identity:`. This predates C3.
  - The `explain` evidence line repeats both content ids; the gate line itself is fine.
  - The `telemetry` section of `docs/guide/commands.md` does not explain the `model=inherit` and
    `none` labels.
- `tests/golden/generate_plan_stage_decisions.py --check` (without `--release`) reports that its
  `AMENDING_PLAN` cases differ in this environment. It does the same at the base, so this milestone
  did not change it, and `tests.test_golden_plan_stage_decisions` passes.
- Carried over, unchanged: the vendored Workflow conformance suites' Git-maintenance hygiene (a
  Workflow repository item), and the deferred items of the earlier milestones, listed in their
  acceptance commits.

**Next action:** release 1.5.0 as above, then merge the 1.5.0 release-notes docs pull request. After
close-out, run `/milestone-plan` for step C4 of "At a glance": auto-merge after acceptance (enable
GitHub auto-merge, wait for the release, close out, stop; section 11.3). It is the next incomplete
roadmap step, and its dependencies C1, C1b and C2 are complete. Plan it from `main`'s tip, with that
base passed explicitly (`/milestone-plan <main tip>`). `/milestone-plan` creates a fresh
`work_items` entry and claims `active_work_item_id`, ready for `PLANNING`.
