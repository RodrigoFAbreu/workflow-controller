# Active Milestone

## Status

**Functional review.** Implementation revision 2 has technical approval (`b82d717`); the checklist is
below. `workflow-controller-squash-merge-tag-versioning` (`docs/ROADMAP.md` step C1,
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
| CP7 Documentation, ADR 0007, cutover rehearsal, full verification | Complete | See below |

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

### CP7 -- Documentation, ADR 0007, cutover rehearsal and full verification

- Guides: `ci-and-releases.md` covers the `PR title` check, a rewritten "Releasing" section (both
  triggers, the type table, the `!` rule, base and range, `INVALID_SUBJECT` and `bump_overrides`,
  the states table), the post-cutover repository settings, and a "Cutover from the version-file
  model" section. The "bump the version" step is gone. `milestone-branches.md` covers
  `merge_method`, the squash flow, the plan's title declaration (the exact `PLAN_TITLE_RE`), title
  and body sync, `pr_title_invalid`, `MERGED_SQUASHED`, close-out, `git branch -D` and
  `merged_before_acceptance` under squash. `runtime.md`, `installation.md`, `concepts.md`,
  `troubleshooting.md` and `development.md` ("Building from a checkout") are updated to match.
- `README.md` (squash merges, releases from the title, "a `docs`/`chore`/`ci` title publishes
  nothing"), `CLAUDE.md` (the plan declares its pull request title), `docs/README.md` (the 1.4.0
  row and the 0007 row), and the note in `docs/ROADMAP.md` section 1.5.
- New ADR `docs/adr/0007-tag-derived-versions-and-squash-merges.md`. ADR 0003 gains a "superseded
  in part" pointer.
- New `docs/releases/1.4.0.md` (Decision D).
- **Plan correction (wording only).** Design H step 6 says 1.3.0's refusal "names the unknown
  trigger". 1.3.0's parser, run from `b5332ab` on the post-cutover block, actually refuses first
  at `milestone_branches.pull_request: unknown key(s) ['merge_method']`. The guides and the 1.4.0
  notes describe what it really does. The sequence and the lock-out are otherwise as the plan says.
- **Cutover rehearsal** (`/tmp/claude-c1/cp7/rehearse.sh`). It used a scratch bare origin holding
  the real tags `v1.1.0`-`v1.3.0` at their real commits and `main` at `455cef0`. The fake forge was
  seeded with `v1.1.1`, `v1.2.0`, `v1.2.1` and `v1.3.0` published, with `v1.1.0` abandoned. The
  milestone head is this branch plus the CP7 working tree. Results:
  1. The `--no-ff` merge into `main` classifies as `NO_CHANGE` at 1.3.0 (`v1.3.0` is published at
     `b5332ab`, an ancestor).
  2. The cutover squash commit on top carries the plan's post-cutover block and
     `dynamic = ["version"]`, titled `feat: squash merges with release versions derived from pull
     request titles (#9)`. The results:
     - `check-title` gives `ok: feat → minor`;
     - `version` gives `1.3.0`;
     - `classify` gives `RELEASE_DUE` 1.4.0 at the squash commit;
     - the full sharded suite with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1` passes (2409 tests,
       5 shards);
     - with `RELEASE_VERSION=1.4.0`, `build`, `verify` and `verify-wheel --tag v1.4.0` pass;
     - the pipx smoke test prints `workflow-controller 1.4.0`.
  3. With `v1.3.0`'s release removed, `classify` at the squash commit gives `RESUME` at
     `b5332ab`, version 1.3.0. In a worktree at `b5332ab`, the build job's `build`, `verify`, pipx
     smoke-test and `checksums` steps ran with `RELEASE_VERSION=1.3.0`. They were read from the
     generated `main.yml` model, and the smoke binary was the scratch pipx one. All passed.
     `publish --commit b5332ab` then created `v1.3.0` with the wheel and `SHA256SUMS`, and the
     next `classify` gives `RELEASE_DUE` 1.4.0.

  The first full-rehearsal attempt failed 6 tests. They were rehearsal artefacts:
  `GIT_COMMITTER_*` was exported and overrode the identities the tests expect. The script now
  sets the scratch clone's `user.*` config instead, and the rerun passed.

Verification:
- `git diff 455cef0 -- .workflow-controller/policy.json pyproject.toml` is empty (I7).
- `python3 tools/ci_workflows.py --check` passes.
- Every golden generator passed with `--check`. `no_policy_lifecycle`,
  `external_implementation_review_decisions` (2.5.1 and 2.6.0) and
  `plan_stage_decisions --release 2.6.0` are current. `plan_stage_decisions` for 2.5.1 still
  differs only in the permitted `AMENDING_PLAN` rows, as at CP6, and
  `test_golden_plan_stage_decisions` passes.
- `test_plan_document_consistency` passes.
- The full sharded run with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, under a reaping-subreaper
  wrapper and with `FORCE_COLOR` unset, passed: 2409 tests, 6 shards.

## Functional review checklist

Round 1, implementation revision 2 (technical approval `b82d717`, reviewed head `669d900`). The
expected results below were measured on 2026-09-29 against `b82d717`.

### Setup

- A scratch directory outside the repository, here `$S` (for example `/tmp/c1-fr`). Nothing in
  these flows writes to the repository, its remote, GitHub or the installed Controller.
- A scratch bare origin with the real tags, `origin/main` (`455cef0`) and the milestone branch:

  ```bash
  R=/home/rodrigo/Workspace/workflow-controller; S=/tmp/c1-fr; mkdir -p "$S"
  git init -q --bare "$S/origin.git"
  git --git-dir="$S/origin.git" fetch -q --no-tags "$R" "+refs/remotes/origin/main:refs/heads/main" \
    "+refs/tags/v*:refs/tags/v*" \
    "+refs/heads/milestone/workflow-controller-squash-merge-tag-versioning:refs/heads/milestone/workflow-controller-squash-merge-tag-versioning"
  git clone -q "$S/origin.git" "$S/work" && cd "$S/work" && git config user.name fr && git config user.email fr@example
  ```
- A fake forge, the repository's own `tests/fake_gh.py`, used as `gh`. Seed it with the published
  releases `v1.1.1`, `v1.2.0`, `v1.2.1` and `v1.3.0` (`v1.1.0` is abandoned). Put its directory
  first on `PATH` and set `FAKE_GH_STATE` (the state JSON), `FAKE_GH_ORIGIN="$S/origin.git"`,
  `FAKE_GH_LOG` and `FAKE_GH_FAIL='{}'`; unset `GH_TOKEN` and `GITHUB_OUTPUT`. The state file is
  `{"repository": "RodrigoFAbreu/workflow-controller", "url": "https://github.com/RodrigoFAbreu/workflow-controller", "next_number": 9, "prs": [], "releases": [...]}`,
  each release `{"tagName": "v1.3.0", "isDraft": false, "url": "...", "assets": [], "title": "v1.3.0", "notes": "v1.3.0"}`.
- To advance the scratch `main` to a commit `C` built with `git commit-tree`:
  `git update-ref refs/heads/tip C; git --git-dir="$S/origin.git" fetch -q "$S/work" "+refs/heads/tip:refs/heads/main"; git fetch -q origin`.
- A local build of the milestone head in a throwaway virtual environment:
  `git clone -q "$S/origin.git" "$S/src" && git -C "$S/src" checkout -q --detach origin/milestone/workflow-controller-squash-merge-tag-versioning && python3 -m venv "$S/venv" && "$S/venv/bin/pip" -q install "$S/src"`.
- Keep Git's automatic maintenance off in the shell that runs the flows (it spawns detached
  processes): `export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=maintenance.auto GIT_CONFIG_VALUE_0=false GIT_CONFIG_KEY_1=gc.auto GIT_CONFIG_VALUE_1=0`.

No test data is needed beyond the real tags and the commits each flow creates.

### Flows

**A. The local build's version.** Run `"$S/venv/bin/workflow-controller" --version`.
Expected: `workflow-controller 1.3.0` and `runtime: package (local build from b82d71766ea4)`. Before
the cutover the static `pyproject.toml` version still wins.

**B. This repository is unchanged (I7).** In the repository, run `inspect .` and `explain .` with
the installed 1.3.0 and with `"$S/venv/bin/workflow-controller"`, and compare the outputs.
Expected: both exit 0, and the outputs are byte-identical. Also:
`git diff 455cef0 b82d717 -- .workflow-controller/policy.json pyproject.toml` is empty.

**C. The milestone's own merge publishes nothing.** In `$S/work`, build the merge commit
`M=$(git commit-tree "<milestone head>^{tree}" -p origin/main -p <milestone head> -m "Merge pull request #8 ...")`,
advance `main` to it, check it out, and run `python3 tools/release.py classify --commit "$M"`.
Expected: `ok: NO_CHANGE: v1.3.0 is published at b5332ab…, an ancestor`, exit 0.

**D. The cutover pull request releases 1.4.0 from `v1.3.0`.** On top of `M`, write the plan's
"Post-cutover policy (the exact CP7 rehearsal content)" block
(`docs/ai-workflow/CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md`) to
`.workflow-controller/policy.json`, and replace `version = "1.3.0"` with `dynamic = ["version"]` in
`pyproject.toml`: exactly 2 files change. Commit it with the subject
`feat: squash merges with release versions derived from pull request titles (#9)` as `Q`,
advance `main`, check it out, then run:
- `python3 tools/release.py version`. Expected: `1.3.0`, the highest reachable release tag.
- `python3 tools/release.py classify --commit "$Q"`. Expected:
  `ok: RELEASE_DUE: 1.4.0 has no tag and no release`, `state=RELEASE_DUE`, `version=1.4.0`,
  `tag=v1.4.0`, exit 0.

**E. The `PR title` rules** (still at `Q`, post-cutover policy). Run
`python3 tools/release.py check-title "<title>"` for each title below:

| Title | Expected |
|---|---|
| `feat: add x` | `ok: feat → minor`, exit 0 |
| `fix(cli): y` | `ok: fix → patch`, exit 0 |
| `feat!: drop z` | `ok: feat! → major`, exit 0 |
| `docs: w` | `ok: docs → none`, exit 0 |
| `feat: x (#12)` | `ok: feat → minor`, exit 0 |
| `docs!: w` | refused, exit 1: a breaking change on `docs` releases nothing; use `feat!` or `fix!` |
| `wip: w` | refused, exit 1: unknown type `wip`, and the allowed types are listed |
| `Update stuff` | refused, exit 1: not a Conventional Commit title, with the expected form |

In the repository itself (the pre-cutover `version_change` policy), `check-title` with any title
prints `ok: the release trigger is version_change; the title is not release input`, exit 0. That
is why PR #8's `PR title` check passes.

**F. An unclassifiable commit refuses, and an override settles it.** At `Q`:
1. Commit a change with the subject `Update readme (#10)` as `U`, advance `main`, and run
   `classify --commit "$U"`. Expected: `refused: INVALID_SUBJECT: 1 commit(s) since v1.3.0 have
   no Conventional Commit subject the policy classifies: <U> 'Update readme (#10)' …`, exit 1.
   Nothing is tagged.
2. Add `"bump_overrides": {"<U>": "patch"}` to the policy's `release` section, commit it as
   `chore: settle the readme commit (#11)` (`V`), advance `main`, and classify `V`. Expected:
   `ok: RELEASE_DUE: 1.4.0 …`: the `feat` still decides, and `U` counts as a patch.
3. Add an override for `Q` (`"none"`) as well, commit it as `chore: override a feat (#12)` (`X`),
   and classify `X`. Expected: `refused: RELEASE_TRANSACTION_REFUSED: release.bump_overrides names
   <Q> ('feat: squash merges …'), which is already classified …`, exit 1. A valid title always wins.

**G. 1.3.0 is locked out after the cutover; the new build is not.** In `$S/work` at `Q`, run
`inspect .` with the installed 1.3.0 and with the local build. Expected: 1.3.0 exits 20 with
`error: .workflow-controller/policy.json: milestone_branches.pull_request: unknown key(s)
['merge_method']; …`. The local build exits 0 and prints the policy line
(`milestone_branches=True release=True trunk=origin/main`). This is why the cutover is its own
pull request, made after this milestone is merged and closed out by 1.3.0, and why 1.4.0 must be
installed before the next milestone.

**H. Squash-mode pull request titles and close-out (automated evidence).** These need a real
squash merge on GitHub, which only the cutover can provide, so they are exercised by the suite.
Run `env -u FORCE_COLOR python3 -m unittest tests.test_pull_request_lifecycle tests.test_release_txn
tests.test_conventional_commit tests.test_version_authority tests.test_repo_policy` (from a
Controller-launched session, under a reaping-subreaper wrapper). Expected: `Ran 292 tests`, `OK`.
They cover `SquashTitleTest`, `SquashReadinessTest` (`pr_title_invalid`), `VerifiedSquashTest`,
`SquashCloseOutTest` (`MERGED_SQUASHED` → `CLOSED`, the explicit `/milestone-plan <tip>` base) and
`MergeModeSquashTest` (merge mode unchanged).

**I. PR #8's checks.** `gh pr checks 8` on the current head. Expected: every check passes,
including the new `PR title`, `validate / tests-result` and `workflow-conformance`.

**J. Documentation.** Read `docs/releases/1.4.0.md`, `docs/guide/ci-and-releases.md` ("The pull
request title decides the release", "An unclassifiable subject: `INVALID_SUBJECT`", "Cutover from
the version-file model") and ADR 0007. Expected: they describe what C-G showed, including the
exact 1.3.0 refusal text.

**Optional, K. Release build at a tag-derived version.** At `Q`, with `RELEASE_VERSION=1.4.0`:
`python3 tools/release.py build`, `verify`, and `verify-wheel dist/workflow_controller-1.4.0-py3-none-any.whl --tag v1.4.0 --commit "$Q"`
pass, and the wheel installed with pipx prints `workflow-controller 1.4.0`. (CP7's rehearsal did
this, and also resumed an interrupted `v1.3.0` publication with 1.3.0's own tooling.)

### Known limitations and out of scope

- A real squash merge, the settings change and the cutover pull request happen only after
  acceptance (plan Design H). They are not part of this round. Flow H covers the squash code paths
  with the suite.
- Auto-merge and waiting for the release (C4), CI flake fixes (C2), the settings file (C3) and
  SignalHub notifications (C5) are later roadmap steps.
- Two self-review observations are left as they are (the external review agreed): trailing
  whitespace in a declared title fails visibly on read-back, and `tag_version` counts abandoned
  tags (the only one, `v1.1.0`, is below `v1.3.0`).
- The Controller leaks zombie child processes during long runs (orphans from Git's detached
  maintenance in test repositories). That is not this milestone's code. It is worked around by one
  step per Controller process, and fixed by the next milestone.
