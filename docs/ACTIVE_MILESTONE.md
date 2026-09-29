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
| CP4 CI workflows | Complete | See below |
| CP5 Pull request title and body in squash mode | Complete | See below |
| CP6 Squash close-out and the explicit base | Complete | See below |
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

### CP4 -- CI workflows

- `tools/ci_workflows.py` renders a fourth file, `.github/workflows/pr-title.yml`: `name: PR title`,
  `pull_request` types `opened`, `edited`, `reopened`, `synchronize`, `contents: read`, one job
  `title` named `PR title` (the check context). Its steps are the pinned `actions/checkout`
  (default depth, `persist-credentials: false`), the pinned `actions/setup-python` 3.12, and
  `python3 tools/release.py check-title "$TITLE"`, with the title in the step's `env` only.
- `main.yml`'s `build` job gains `env: RELEASE_VERSION: ${{ needs.release-plan.outputs.version }}`.
  Its `build` and `verify` run lines are 1.3.0's byte for byte. Its pipx smoke test now compares
  with `workflow-controller $RELEASE_VERSION` (`RELEASE_PIPX_SMOKE`); `release-plan`'s outputs are
  unchanged. `validate.yml` renders byte-identically to the base (`PIPX_SMOKE`, the local build's
  version, unchanged in text). `ci.yml` is unchanged.
- `tests/test_ci_workflows.py`: the generated set, `PrTitleWorkflowTest` (triggers, read-only, the
  check context, no `${{` in any `run:`, pins, credentials), the build job's env, the release
  smoke test and the unchanged `validate` smoke text against a literal copy of 1.3.0's.
- `tests/test_trunk_orchestration_e2e.ReleaseHistoryTest`: the 1.3.0-tooling `RESUME` test now
  takes `RELEASE_VERSION` from the generated build job's `env` alone; CP3 supplied it by hand.

Verification: `python3 tools/ci_workflows.py --check` passes. The narrow modules pass
(`test_ci_workflows`, `test_trunk_orchestration_e2e`, `test_plan_document_consistency`,
`test_write_containment`, `test_package_structure`, `test_release_tools`: 178 tests). The full
sharded run with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, under a reaping-subreaper wrapper and with
`FORCE_COLOR` unset, passed: 2351 tests, 6 shards.

### CP5 -- Pull request title and body in squash mode

- `controller/forge.py`: `PR_FIELDS` gains `title,body`, and `PullRequest` gains `title` and
  `body`. The new `edit_pr(number, *, title=None, body=None)` runs `gh pr edit <n> [--title T]
  [--body B] --repo …`, re-reads the pull request with `view_pr`, and refuses (`ForgeError`)
  unless the re-read shows the new values. Bodies are compared with `same_text`, which ignores
  CRLF line ends and trailing newlines. The module docstring names this one edit; the forge still
  has no merge, close, delete, comment or `gh api` operation.
- `controller/milestone_branch.py`, squash mode only (`merge_method: "squash"` in the binding's
  policy):
  - `plan_title` extracts ``Pull request title: `<title>` ``: exactly one line starting with the
    prefix, matching the anchored regex whole. `declared_title` reads `plan_path` from `HEAD`'s
    committed state and the plan from `HEAD:<plan_path>` (`gitrepo.show`), never the working
    tree. `title_problem` validates with `conventional_commit.bump` against the `change_types` of
    the policy committed at `HEAD` under `conventional_commit`, else the grammar alone.
  - Creation: the declared title, else the work-item id; the body is `squash_body` (the committed
    `plan_path`, the marker, no trailer-shaped line).
  - Every `PR_OPEN` step before acceptance edits a differing title to the declared one
    (`_sync_title`). A `READY` record is never edited.
  - Readiness, after conditions 4-6 and before condition 7 (`_sync_for_readiness`): the declared
    title wins; without one a valid current title is kept, otherwise `pr_title_invalid` (new gate
    code), before any checks are read. The body gains the "Accepted at" line. Any edit ends the
    step at `checks_pending`, without `gh pr ready` and without writing `READY`.
  - `Gate` gains `merge_method`; `post_acceptance_commits`, `integration_required` and
    `merge_pull_request` name "Squash and merge" in squash mode.
- `controller/decision.py`: `pr_title_invalid` joins `BRANCH_GATE_TEXTS`, and
  `BRANCH_GATE_TEXTS_SQUASH` replaces the three merge-button texts for a squash-mode gate.
- Merge mode (no `merge_method`, or `"merge"`) is byte-identical to 1.3.0: title, body, gate
  texts, and no `pr edit`.
- `tests/fake_gh.py` returns `title`/`body` and implements `pr edit` (a closed or merged PR
  refuses). Tests: `test_forge.EditTest`; `test_pull_request_lifecycle` `PlanTitleTest` (the
  extraction cases and this plan's own header line), `SquashTitleTest`, `SquashReadinessTest`
  (the readiness-order cases with `ready_requires_green_checks: true`, the call log showing no
  `gh pr ready` after an edit, the `git interpret-trailers --parse` check on every rendered
  body), `MergeModeUnchangedTest` and `GateTextTest`.

Verification: the narrow modules pass (`test_forge`, `test_pull_request_lifecycle`: 133 tests;
`test_write_containment`). The first full run caught `same_text`'s `str.replace`, which the
write-containment scan flags; it now uses `re.sub`. The full sharded run with
`CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, under a reaping-subreaper wrapper and with `FORCE_COLOR`
unset, then passed: 2374 tests, 6 shards. The post-cutover scratch clone (a full clone with the
real tags, this diff plus the cutover's dynamic `pyproject.toml` and `CONVENTIONAL_POLICY`) also
passed: 2374 tests, 5 shards.

### CP6 -- Squash close-out and the explicit base

- `controller/milestone_branch.py`:
  - `MERGED_SQUASHED` is a new non-terminal state, part of `MERGED_PR_HANDLING`. It can be reached
    from `PR_PLANNED`, `PR_OPEN`, `READY` and `PR_CLOSED_UNMERGED`, and it leads to `CLOSED`.
    `CLOSE_OUT_STATES` is `(MERGED, MERGED_SQUASHED)`.
  - `_merged_handling` calls the new `verified_squash` only when the binding's policy is in squash
    mode, and only for a merged head that is not on the trunk. Otherwise it writes 1.3.0's
    `MERGED_REWRITTEN`, record and gate byte for byte.
  - `verified_squash` checks Design F's conditions in the order 1, 2, 3, 5, 4:
    1. the merge commit exists and is on the fetched trunk;
    2. it has one parent `p`;
    3. its subject is `<title> (#<number>)`;
    5. its author name, email, date and full message are not `h`'s;
    4. its tree is `h`'s when `p` is an ancestor of `h`; otherwise it is the tree
       `git merge-tree --write-tree p h` writes.

    Any configured `merge.<name>.driver` means "not verified", and `merge-tree` is never run.
    Git older than 2.38 on that path refuses, naming the floor. So does a failing `merge-tree`.
    Neither writes anything.
  - Close-out, from the branch and from the trunk, runs from both states. From `MERGED_SQUASHED` it
    checks that the squash commit, not `merged_head`, is on the trunk. Merge-mode refusal texts
    are unchanged.
  - `status`, `inspect` and `explain` name the state. `explain` predicts "the squash-merged
    binding closes out".
  - The explicit base: `Proceed.base` is the trunk tip that a passed trunk start proved equal to
    `<remote>/<trunk>`. A close-out carries it on. Every prediction gains a `base` key, which is
    set only for a trunk start that would pass.
- `controller/decision.py`: `decide_no_work_item(managed_repo, *, base=None)` names
  `/milestone-plan <base>` and says so in its reason. The triple is unchanged.
- `controller/job.py` passes the preflight's `base`, and `controller/cli.py`'s `explain` passes
  the prediction's. Without a policy, or with branches disabled, the command stays bare, and
  `no_policy_lifecycle.json` is byte-unchanged.
- The open question, measured: the expected-outcome table, the resume lookup, routing,
  `classify_selected_action` and property 5 all key on the command token, so no table needed a
  new row. `ArgumentBearingBootstrapTest` pins this.
- `controller/gitrepo.py` adds read-only helpers: `commit_parents`, `tree_of`, `commit_subject`,
  `commit_identity`, `git_version`, `merge_drivers` and `merge_tree`. `merge_tree` tells a
  conflict (exit 1, a tree and the conflicted paths) apart from an unmergeable argument (exit 1
  with a message only), and refuses the latter.
- **A deviation from the plan's text, for CP7's guide.** Design F says the `--new-pr`
  continuation after `MERGED_BEFORE_ACCEPTANCE` "still converges" by marking the PR ready and
  squashing. The measurement shows otherwise. The first squash and the continuation's acceptance
  both add the item's block to `WORKFLOW_STATE.json`, so the continuation conflicts with the
  trunk, and GitHub disables the squash button.
  - What converges: the human resolves the conflict on GitHub (a merge of the trunk into the
    branch there), then squashes. The squash verifies on the ordinary path, because `p` is an
    ancestor of the resolved head. Close-out then refuses once and names
    `git merge --ff-only <resolved head>` for the local branch, after which it closes.
  - Without a conflict, the `merge-tree` path decides.
  - No mechanism was added. Both paths are tested.
- Tests:
  - `test_milestone_branch`: the state model, 12 states and the exact transition table.
  - `test_pull_request_lifecycle`:
    - `MATRIX` rows and cells `squashed_branch`/`squashed_trunk`;
    - `VerifiedSquashTest`, each condition over a scratch repository: the one-commit rebases with
      a bare and a numbered title, `h`'s identity with a one-second control, the different tree,
      the integration path, the conflict, the merge driver whose marker never appears (and does
      once `merge-tree` itself runs), and Git 2.37 or a failing `merge-tree` through the runner;
    - `SquashCloseOutTest`: close-out from the branch and the trunk, with `dirty_tree`,
      `unmerged_commits` and `fast_forward_trunk`; the integration path; refuse-then-verify with
      an old Git or a failing `merge-tree`, writing nothing; rebase, bare-title, edited-subject
      and legacy-subject merges giving `MERGED_REWRITTEN`; both `MERGED_BEFORE_ACCEPTANCE
      --new-pr` paths; and the reporting views;
    - `MergeModeSquashTest`: the same GitHub squash in merge mode is 1.3.0's `MERGED_REWRITTEN`,
      and `verified_squash` is never consulted.
  - `test_gitrepo.SquashReadTest`, `test_decision` (the one-argument form),
    `test_job_validation.ArgumentBearingBootstrapTest`, and `test_trunk_preflight` (`explain`
    names the tip). The bootstrap assertions of the existing step tests now expect the tip.
  - `test_trunk_orchestration_e2e.SquashLifecycleTest`, end to end: the declared title, the body
    sync, "Squash and merge", close-out, the bootstrap naming the squash commit, and the next
    bind at it. The policy scenarios expect `/milestone-plan <tip>`, and the no-policy scenario
    stays bare.

Verification: the narrow modules pass (`test_milestone_branch`, `test_pull_request_lifecycle`,
`test_trunk_orchestration_e2e`, `test_decision`, `test_job_validation`, `test_trunk_preflight`,
`test_gitrepo`, `test_cli`, `test_job`, `test_no_rewrite_invariants`, `test_write_containment`,
`test_package_structure`, `test_golden_plan_stage_decisions`, `test_plan_document_consistency`:
787 tests). Every golden generator was run with `--check`. `no_policy_lifecycle`,
`external_implementation_review_decisions` (2.5.1 and 2.6.0) and `plan_stage_decisions --release
2.6.0` are current. `plan_stage_decisions` for 2.5.1 differs in its `AMENDING_PLAN` rows at `HEAD`
as well: that is the permitted difference `test_golden_plan_stage_decisions` documents, and the
test passes. The full sharded run with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, under a
reaping-subreaper wrapper and with `FORCE_COLOR` unset, passed: 2409 tests, 5 shards. The
post-cutover scratch clone (a full clone with the real tags, this diff plus the cutover's dynamic
`pyproject.toml` and `CONVENTIONAL_POLICY`) also passed: 2409 tests, 5 shards. There the
Controller reports `1.3.0`.
