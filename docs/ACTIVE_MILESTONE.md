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
| CP2 Tag-derived Controller version | Complete | See below |
| CP3 Release classification from commit types | Complete | See below |
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

### CP2 -- Tag-derived Controller version

- `controller/version.py`: `static_pyproject_version` (`None` when dynamic or absent),
  `tag_version(root, rev="HEAD")` (the highest strict `vMAJOR.MINOR.PATCH` tag reachable from
  `rev` through `git for-each-ref --merged`; `0.0.0` with none or on an unborn `HEAD`; a
  non-work-tree-top root, an unresolvable rev or a failing `git` raise `ValueError`; `GIT_*`
  environment variables are dropped), `source_version` (static, else tags) and
  `local_build_version` (static, else tags, else `0.0.0` outside Git).
  `parse_pyproject_version` is replaced by `parse_static_version`.
- `setup.py`: for `dynamic = ["version"]`, `setup()` receives the release tag's version
  (`WORKFLOW_CONTROLLER_RELEASE_TAG`, a malformed tag fails the build) or `local_build_version`.
  A static version keeps setuptools' own read.
- `controller/identity.py`: a pinned source snapshot's version is its own static version, else
  the origin checkout's tags at the snapshot's commit (`HEAD` when dirty).
  `controller/handoff.py`: the committed version is `HEAD`'s static version, else `HEAD`'s tags.
- `tools/release.py`: `version` prints `local_build_version` (no policy needed; the removed
  `policy_version` was one of the `read_committed_version` callers CP1 named);
  `verify-wheel --tag` expects the tag's version, `--local` the local build version.
- `tests/fixtures.py`: `build_checkout()` always writes the static `CONTROLLER_VERSION` line
  (`dynamic_version=True` writes the dynamic one) and never tags. `CheckedOutVersionTest` and
  `VersionCommandTest` no longer copy the real policy (neither subcommand reads one), and a
  scan test keeps any test from copying `.workflow-controller/` into a clone.
- New tests: `test_version_authority` (`TagVersionTest`, `FixtureTest`, the pre-or-post-cutover
  consistency test, dynamic source/pinned/dirty-pinned runtimes and handoff),
  `test_buildinfo.DynamicVersionBuildTest` (local wheel at the tag, `0.0.0` without one or
  outside Git, release wheel at the injected tag, malformed tags refused, editable install
  writes no `BUILD_INFO.json`), `test_release_tools.DynamicVersionWheelTest`.

Verification: `--version` in this checkout still reports `workflow-controller 1.3.0`. The full
sharded run with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, under a reaping-subreaper wrapper and
with `FORCE_COLOR` unset, passed: 2308 tests, 6 shards, no failures. The post-cutover run (a full
clone with the real tags, this checkpoint's diff plus the cutover's `pyproject.toml` and
`CONVENTIONAL_POLICY` committed on top) also passed: 2308 tests, 5 shards, the packaged-runtime
tests included. There the Controller reports `1.3.0` from `v1.3.0`.

### CP3 -- Release classification from commit types

- `controller/release_txn.py`: under `conventional_commit`, `classify` computes the version at
  `C` (Design C). The base is the highest matching ancestor tag (`0.0.0` without one). An
  unsettled base (no release or a draft, and not in `abandoned_tags`) keeps the base version, so
  the unchanged rows give `RESUME` at its commit. Otherwise `_range_bump` walks the first-parent
  range since the base:
  - a legacy commit (no policy, or `version_change`) contributes nothing;
  - a Conventional one contributes its subject's bump under its own committed `change_types`;
  - an unclassifiable subject takes `C`'s `bump_overrides` entry, else it is named in the new
    failing state `INVALID_SUBJECT`. The outputs are the base version and tag, and every such
    commit is listed;
  - an override naming a classifiable or legacy commit refuses, and so does a range commit whose
    committed policy cannot be read and has no override.

  No base tag and no bump is `NO_CHANGE` at `0.0.0` with no forge read. The base tag's release
  view is reused when the version stays at the base. `INVALID_SUBJECT` is first in `STATES` and
  not in `SUCCESS_STATES`. The `version_change` path is unchanged.
- `build(ctx, commit, version)` and `verify(ctx, commit, version)` go through `build_version`.
  Under `conventional_commit` the classified version is required and must be SemVer. Under
  `version_change` it is optional and must equal the committed version.
- `controller/gitrepo.py`: `first_parent_subjects(base | None, commit)`, a single read-only
  `rev-list --first-parent --reverse --format=%H%x00%s` that yields `(commit, subject)` oldest
  first. The subject is Git's `%s`.
- `tools/release.py`: `build` and `verify` read `RELEASE_VERSION` from the environment. Their
  command lines stay byte-identical to 1.3.0's; a missing or empty value refuses in one line
  under the new trigger.
- Tests:
  - `test_release_txn.ConventionalClassificationTest` covers every type and range case in the
    plan, `INVALID_SUBJECT` and its override, the override refusals, legacy commits, this
    repository's history since `v1.3.0` (`NO_CHANGE` at the milestone merge, then `RELEASE_DUE`
    1.1.0), the unsettled or draft base, `BASELINE_UNRELEASED`, `ABANDONED_VERSION`, both
    collisions, `ALREADY_RELEASED`/`RELEASE_MISMATCH`, `0.1.0` with no base, and
    `NO_CHANGE` at `0.0.0` with no tag or release. It also covers the unparsable historical
    policy.
  - `ConventionalTransactionTest` reruns the whole `TransactionTest`, the concurrent-tag races
    included, under the new trigger. It also checks the build version rules and that publish
    refuses a build of another version.
  - `test_release_tools.ReleaseTransactionCliTest` checks `RELEASE_VERSION` and 1.3.0's bare
    parsers. `test_gitrepo` covers the range helper.
  - `test_trunk_orchestration_e2e.ReleaseHistoryTest` adds the cutover history end to end. It
    also adds a `RESUME` at a base commit carrying a 1.3.0-shaped `tools/release.py`, whose
    `build`/`verify` steps run as the generated `main.yml` build job declares them.

Verification: the narrow modules pass (`test_release_txn`, `test_release_tools`,
`test_trunk_orchestration_e2e`, `test_gitrepo`, `test_no_rewrite_invariants`,
`test_write_containment`, `test_package_structure`, `test_ci_workflows`, `test_repo_policy`,
`test_version_authority`, `test_plan_document_consistency`: 350 tests). The full sharded run with
`CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, under a reaping-subreaper wrapper and with `FORCE_COLOR`
unset, passed: 2342 tests, 5 shards. The post-cutover scratch clone passed too: 2342 tests, 6
shards. That clone is a full clone with the real tags, carrying this diff plus the cutover's
dynamic `pyproject.toml` and `CONVENTIONAL_POLICY`, and there the Controller reports `1.3.0`.
