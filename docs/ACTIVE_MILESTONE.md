# Active Milestone

## Status

**Implementing.** `workflow-controller-squash-merge-tag-versioning` (`docs/ROADMAP.md` step C1,
section 11.1 "Squash merges and PR-title versions"). The plan is
`docs/ai-workflow/CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md`, revision 3, approved at
`614b398` (`EXTERNAL_APPROVE`). The base commit is `455cef0`. Governing workflow version `2.2`,
lifecycle authority Workflow 2.6.0, driving Controller the installed 1.3.0.

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-workflow-2-6-integration.md`.

## Goal

Squash merges only; the pull request title is a Conventional Commit; the commit type decides the
release; the Git tag is the only version authority; the existing release safety stays; close-out
understands a squash merge (`MERGED_SQUASHED`); and a cutover that never locks out this milestone
(plan Design H). This milestone never changes `.workflow-controller/policy.json` or
`pyproject.toml`'s version line (I7).

## Checkpoint progress

| Checkpoint | Status | Notes |
|---|---|---|
| CP1 Conventional Commit titles and the policy schema | Complete | See below |
| CP2 Tag-derived Controller version | Not started | |
| CP3 Release classification from commit types | Not started | |
| CP4 CI workflows | Not started | |
| CP5 Pull request title and body in squash mode | Not started | |
| CP6 Squash close-out and the explicit base | Not started | |
| CP7 Documentation, ADR 0007, cutover rehearsal, full verification | Not started | |

### CP1 -- Conventional Commit titles and the policy schema

- `controller/conventional_commit.py` (new): SignalHub's `SUBJECT_RE`, `parse` and
  `bump(subject, change_types)`. The refusal is `errors.InvalidTitleError` (code `INVALID_TITLE`),
  also exported under the plan's name `conventional_commit.InvalidTitle`.
- Import order (the plan's open question): `repo_policy` validates `change_types` keys with the
  grammar's type alphabet and the bump values, so `conventional_commit` sits **before**
  `repo_policy` (`buildinfo, version, errors, conventional_commit, repo_policy, ...`).
- `controller/repo_policy.py`: trigger `conventional_commit` (no `version_source`, required
  `change_types`, optional `bump_overrides`, `version_scheme` semver), optional
  `milestone_branches.pull_request.merge_method` (`merge` default, `squash`) and the cross-field
  rule. `Release.version_source_kind/path` are `None` under the new trigger; `read_version` and
  `read_committed_version` refuse there, naming the trigger. Their callers (`release_txn`,
  `tools/release.py version`) still call them unconditionally; CP2 and CP3 remove them.
- `tools/release.py check-title TITLE`, reading the policy committed at `HEAD`.
- `tests/fixtures.py`: `LEGACY_POLICY` and `CONVENTIONAL_POLICY`, the two plan blocks. The policy
  refusal tests now mutate `LEGACY_POLICY` instead of the real file, and the reference test
  accepts exactly the pre- or post-cutover content, so `test_repo_policy` holds on both sides of
  the cutover.
- `.workflow-controller/policy.json` is byte-unchanged.

Verification: `tests.test_conventional_commit`, `test_repo_policy`, `test_release_tools`,
`test_package_structure`, `test_write_containment`, `test_release_txn` and `test_milestone_branch`
pass. The full sharded run (`python3 tools/run_tests.py`, 2279 tests, 6 shards) passed but for six
environmental failures, all of which pass on re-run with the environment corrected:
- five orphan-reap checks (`test_resume` `OrphanWorkerTest`, `UnreconcilableOrphanTest`,
  `EndToEndInterruptionTest`, `BootstrapEndToEndInterruptionTest`, and `test_lock`
  `InheritedDescriptorTest`). This session runs under a Controller, a child subreaper, so orphans
  stay zombies. They pass under a reaping-subreaper wrapper;
- `test_evidence.ContentDriftedRealTest.test_a_parent_directory_replaced_by_a_link_after_the_gate_is_rendered`,
  which matches a traceback in stderr literally. The session's `FORCE_COLOR=3` colours it. It
  passes with `FORCE_COLOR` unset.
