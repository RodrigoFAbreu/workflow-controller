# Active Milestone

## Status

**Implementing.** `workflow-controller-trunk-branch-pr-release-orchestration`, governing workflow
version `2.2`, base commit `00c3b7136dae2ca1993af91c999df48ad376f433` ("Prepare the 1.1.1 patch
release"). Plan revision 10 was approved by both plan-review stages (approval commit `bb7839a`).
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for the phase and for each checkpoint's
status. The installed release Controller 1.1.1 orchestrates this milestone, under the installed
Workflow 2.5.1.

## Goal

Establish a reusable, lightweight trunk-based development and release model, with
`workflow-controller` as its first adopter:

- **generic capability** that any Workflow-managed repository can reuse: milestone branch binding,
  a Controller-owned GitHub boundary, the Draft PR lifecycle, trunk-drift detection, a repository
  release policy, and a crash-safe release transaction with explicit collision and recovery
  states;
- **reference configuration** for this repository only: trunk `main`, branches
  `milestone/<work-item-id>`, version source `pyproject.toml`, tag `v{version}`, a wheel plus
  `SHA256SUMS` published as a GitHub Release;
- **one version authority**, `pyproject.toml`'s static `[project].version`;
- **release on version change**: a push to `main` releases exactly when the version is new. The
  tag is created only after validation, at the validated commit, and is never moved;
- **Workflow stays authoritative**: under Workflow 2.5.1 the Controller detects trunk drift and
  fails closed at readiness. Binding to Workflow 2.6.x is a separate follow-up milestone.

## Plan

- Plan: `docs/ai-workflow/CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md`
- Registry: `docs/ai-workflow/registry/workflow-controller-trunk-branch-pr-release-orchestration-registry.json`
  (checkpoints `CP1`-`CP10`; `CP10` is terminal)
- Requirement map: `docs/ai-workflow/requirements/workflow-controller-trunk-branch-pr-release-orchestration-mapping.json`

The previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-release-runtime-observability.md`. Its deferred
follow-ups are listed there and in `docs/ROADMAP.md` section 1.4. None of them is in this
milestone's scope.

## Baseline

Recorded at CP1 start, before the first edit, on a detached worktree at `bb7839a`:
- `python3 -m unittest discover -s tests -t .` (no `PYTHONPATH=.`): 1275 tests, OK (6 skipped),
  98 s;
- the seven conformance suites, from `scripts/`: all OK. `workflow_fingerprint_test.py` 218,
  `workflow_integration_test.py` 260 (1 skipped), `workflow_acceptance_matrix_test.py` 146
  (18 skipped), `workflow_state_completion_obligations_test.py` 106,
  `workflow_fingerprint_generalization_test.py` 79, `workflow_test_harness_test.py` 19.
  `workflow_state_test.py`'s summary line was not captured in the baseline run: its captured
  output tail shows its regression checks passing, and the whole run exited 0;
- `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime`: 9 tests,
  OK.

## Checkpoint progress

- **CP1 -- single version authority: complete.**
  - `pyproject.toml` declares `version = "1.1.1"` statically. `dynamic` and
    `[tool.setuptools.dynamic]` are gone.
  - `controller/version.py` holds no value. It is a stdlib-only resolver that imports nothing from
    `controller`:
    - `source_version(code_root)` reads `<code_root>/pyproject.toml`'s `[project].version`;
    - `package_version(code_root)` reads the one `workflow-controller` distribution installed in
      `code_root` itself whose `RECORD` lists `controller/__init__.py`;
    - `parse_pyproject_version` is shared with `handoff`.
    All three raise `ValueError` naming the failure. `version.SEMVER_RE` duplicates
    `buildinfo.SEMVER_RE`, since neither leaf may import the other; a test pins them equal.
  - `identity.resolve_runtime` carries a `version` on `RuntimeResolution`:
    - `package`: the distribution metadata version. It must equal `BUILD_INFO.json`'s version,
      and a disagreement is `unidentified` ("partially upgraded"). Missing metadata is
      `unidentified` too;
    - `source`: `pyproject.toml`'s version. An unreadable version is `unidentified`;
    - `unidentified`: whichever reader succeeds, else `unknown`.
  - `ControllerIdentity.version` is a required keyword field with no import-time default.
    `materialise` records the snapshot's own version in `SOURCE_PIN.json` and in the
    `controller_runtime` block: a source snapshot's own `pyproject.toml`, or a package snapshot's
    own `controller/BUILD_INFO.json`. `pin()` reads a pinned child's version only from
    `SOURCE_PIN.json`. A missing or non-semver value raises `SourceSnapshotError`
    (`raised_by: pin`), with no fallback.
  - `cli.version_text` prints the resolved runtime's version (`unknown` if the identity cannot be
    resolved). `--version` line 1 is unchanged in form.
  - `handoff` reads the approved version from `HEAD:pyproject.toml` with `tomllib`.
  - `setup.py`'s build hook takes the version from `self.distribution.get_version()`.
  - `tools/release.py`: `version`, `verify-tag` and `verify-wheel` read
    `version.source_version(<repository root>)`. `main()` takes a `repo_root` keyword.
    `verify-tag` now requires the static form, and refuses a dynamic version or a
    `[tool.setuptools.dynamic]` version.
  - Tests:
    - new `tests/test_version_authority.py` (19 tests) covers the single authority, the readers,
      a stale `egg-info` and another distribution on `sys.path` being ignored by a source runtime,
      the package metadata version, metadata/BUILD_INFO disagreement, missing metadata, pinned
      source and package children, pin-version refusal, a dirty snapshot's own version, and
      handoff's committed read. It runs in a new `trunk` CI shard; `.github/workflows/validate.yml`
      was regenerated;
    - `test_release_tools.CheckedOutVersionTest` rewrites the version in a disposable checkout and
      sees `version`, `verify-tag` and `verify-wheel` all follow it. `verify-tag`'s refusals are
      rewritten for the static form;
    - `test_packaged_runtime` case 6 now also compares `tools/release.py version`.
      `_write_version` rewrites `pyproject.toml`;
    - `fixtures.CONTROLLER_VERSION` replaces every test's `version.__version__`.
      `fixtures.build_package_tree` writes a `*.dist-info` by default (`metadata_version`,
      `dist_info`). Every test `ControllerIdentity` passes `version=`.
  - README: the two pointers to `controller/version.py` now name `pyproject.toml`. The full
    releasing rewrite is CP10's.
  - Two existing tests were adjusted, not weakened. The pathspec-dirty test appends to
    `pyproject.toml` rather than replacing it, since a checkout with no version is now
    `unidentified`. The "pin without `runtime_kind`" test keeps `version`, since every pin carries
    it and there is no fallback.
  - Verified:
    - narrow: `tests.test_version_authority`, `tests.test_identity`, `tests.test_release_tools`,
      `tests.test_buildinfo`, `tests.test_packaged_runtime` (packaging required), `tests.test_cli`,
      `tests.test_handoff`, `tests.test_job`, `tests.test_job_validation`,
      `tests.test_package_structure`, `tests.test_ci_workflows`,
      `tests.test_plan_document_consistency`, all OK;
    - full: `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest discover -s tests -t .`
      ran 1295 tests, OK (6 skipped), in 99 s. An earlier full run caught one real failure:
      `test_write_containment`'s package-wide scan flags any `.replace(...)` call, and
      `package_version` normalised paths with `str.replace`. It now uses `PackagePath.as_posix()`;
    - the seven conformance suites, all OK: `workflow_fingerprint_test.py` 218,
      `workflow_state_test.py` 853, `workflow_test_harness_test.py` 19,
      `workflow_integration_test.py` 260 (1 skipped), `workflow_acceptance_matrix_test.py` 146
      (18 skipped), `workflow_state_completion_obligations_test.py` 106,
      `workflow_fingerprint_generalization_test.py` 79;
    - `python3 tools/ci_workflows.py --check`: passes.
- **CP2 -- repository policy: complete.**
  - New `controller/repo_policy.py`, after `errors` in the dependency order. It imports only
    `buildinfo` (`SEMVER_RE`) and `errors`.
  - `parse_policy(raw)` validates the exact bytes and returns a frozen `RepositoryPolicy`: the raw
    bytes, their SHA-256 (the future binding snapshot), trunk, forge, `MilestoneBranches` and
    `Release`. The first broken rule raises the new `InvalidRepositoryPolicyError`
    (`INVALID_REPOSITORY_POLICY`). Its evidence names the path, the field and the problem, plus
    the revision when read from Git. The rules:
    - UTF-8 JSON object, no duplicate key, no unknown key and no missing required key at any
      level. `abandoned_tags` and a command's `env` are the only optional keys;
    - `schema_version` is the integer `1`;
    - placeholders are the closed set of the plan, and each templated field admits a subset:
      branch format `{work_item_id}`; tag format `{version}`; build `{version}`/`{tag}`/
      `{commit}`; verify also `{artifact}`; artifact paths `{version}`; publication
      `{version}`/`{tag}`/`{commit}`. An unmatched brace refuses too;
    - `branch_format` holds `{work_item_id}` exactly once. It must render a valid branch
      (`git check-ref-format --branch`) for every id matching `^[a-z0-9][a-z0-9_-]{0,63}$`. The
      module documents why a fixed sample of ids decides that exactly: every ref rule is decided
      by the literal text, except a `.lock` component suffix, which an id completes only as a
      substring of `lock`, so every such substring is sampled. It must not render the trunk name.
      Also refused, since Git cannot create such a branch next to the trunk: a name that is a
      `/`-component prefix or extension of the trunk. This is decided exactly over the id
      alphabet;
    - `tag_format` holds `{version}` exactly once and no other placeholder (invertible), and
      renders valid tags (`check-ref-format refs/tags/...`);
    - each `abandoned_tags` entry must parse back through `tag_format` to a semver version. No
      duplicates;
    - `forge.repository` is `OWNER/NAME`, `trunk.branch` a valid branch, `trunk.remote` a plain
      remote name;
    - `pull_request.draft` must be `true`: only Draft PRs are implemented;
    - artifact and version-source paths are normalised relative paths. `checksums` is a plain
      file name that no artifact shares.
  - Closed adapter registries, each unknown `kind` naming the known ones: version source
    `pyproject` (static `[project].version` with `tomllib`; a `dynamic` or missing version
    refuses), scheme `semver` (a total-order key), `command` for build and verify (argv and env
    templates, `Command.render`, no shell), publication `github_release`, forge `github`, trigger
    `version_change`.
  - A `git check-ref-format` outcome other than valid or invalid refuses (I9). `--branch` reports
    an invalid name as exit 128 with a fixed message, read under `LC_ALL=C`.
  - `read_committed_policy(repo_root, rev="HEAD")` never reads the working tree. It runs
    `git ls-tree -z <rev> -- .workflow-controller/policy.json`: exit 0 with no output is absent
    (`None`). A present entry must be a regular-file blob, and is read with `git cat-file blob`
    by the listed object id. This is `git show <rev>:<path>`'s content, bound to the tree entry
    just listed, so a moving `HEAD` cannot mix two trees. Any other Git outcome refuses and is
    never "absent", including an unborn `HEAD`, which CP8's probe classifies itself.
  - `read_committed_version(repo_root, policy, rev)` reads the policy's version source from the
    committed tree, for CP4.
  - `milestone_branches_enabled(policy)` and `release_enabled(policy)` are the two independent
    switches. `None` (no file) means both off.
  - `.workflow-controller/policy.json` holds the plan's reference content. A test compares it with
    the plan's JSON block. Nothing reads it at runtime until CP6/CP8.
  - Tests: new `tests/test_repo_policy.py` (40 tests) in the `trunk` CI shard, with
    `validate.yml` regenerated. `test_package_structure.DEPENDENCY_ORDER` gains `repo_policy`.
  - Verified:
    - narrow: `tests.test_repo_policy`, `tests.test_package_structure`,
      `tests.test_write_containment`, `tests.test_ci_workflows`, all OK;
    - full: `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest discover -s tests -t .`
      ran 1335 tests, OK (6 skipped), in 101 s;
    - `python3 tools/ci_workflows.py --check`: passes.
- **CP3 -- Git and GitHub boundaries: complete.**
  - New `controller/gitrepo.py` and `controller/forge.py`, after `repo_policy` in the dependency
    order. Both import only `errors`, and `forge` imports `gitrepo`'s runner. New errors:
    `GitOperationError` (`GIT_OPERATION_FAILED`), `ForgeError` (`FORGE_ERROR`) and its subclass
    `ForgeUndecidableError` (`FORGE_UNDECIDABLE`).
  - `gitrepo`: module functions taking the repository root and an injectable `runner`
    (`argv -> CompletedProcess`, bytes). The default, `subprocess_runner(env)`, closes stdin and
    sets `LC_ALL=C`, `GIT_TERMINAL_PROMPT=0` and a 600 s timeout. A failed call raises
    `GitOperationError` with argv, exit code and stderr. A missing `git` and a timeout raise it
    too.
    - Reads: `head_state` (branch or detached, `None` commit when unborn), `ref_commit`,
      `tag_commit`, `is_ancestor`, `ahead_behind`, `merge_base`, `first_parent_log` (oldest
      first), `tracked_changes`, `common_dir`, `remote_url`, `ls_remote` (peels annotated tags:
      it asks for each ref's `^{}` line explicitly, because a pattern does not match it, and
      keeps exact names only), `show` (absent path is `None`, unknown revision refuses),
      `commit_trailers` (`%(trailers:only,unfold)`, the last-paragraph rule), and
      `worktree_branches` (`git worktree list --porcelain -z`, detached is `None`; TBR-R4-002).
      An "absent" answer needs the expected exit code, empty stdout and the expected stderr (empty,
      or `No such remote`). Any other outcome refuses (I9).
    - `fetch` accepts only non-forced refspecs into `refs/remotes/<remote>/`, or exactly
      `refs/tags/*:refs/tags/*`, which fails rather than overwrites a differing local tag. It
      checks them before running anything. It passes `--no-tags` and never `--prune`.
      `fetch_branch` is the one-branch form.
    - `create_and_switch` (refuses an existing branch), `switch` (clean tracked tree only),
      `fast_forward` (`merge --ff-only`, with `HEAD` on the branch).
    - `push_branch`: `ls-remote`, then, if the remote has the branch, fetch it and require it to be
      an ancestor of the local tip. Otherwise it refuses (`reason: not_fast_forward`) without
      contacting the remote again. The push is `refs/heads/B:refs/heads/B`, no `+`.
    - `create_annotated_tag` refuses an existing local tag. `push_tag` refuses when the remote
      already has the tag at any commit, the same one included (`reason: exists`,
      `remote_commit`). A rejected push is re-read and reported, never retried.
    - `merge_trunk`: clean tracked tree, `git merge --no-ff --no-edit refs/remotes/<remote>/<trunk>`.
      On conflict it lists the unmerged paths, runs `git merge --abort` and refuses. Nothing in
      the lifecycle calls it.
  - `forge`: the `Forge` protocol and `GhForge(repository, runner, gh="gh")`. Every call passes
    `--repo OWNER/NAME`, except `gh repo view`, which has no such flag and takes the repository
    as its positional argument. Typed results: `RepositoryIdentity`, `PullRequest`, `Checks`
    (`checks` or `no_checks`), `Release` and `ReleaseAsset`.
    - `repository_identity`, `verify_repository` (a mismatch is `ForgeError`), `list_prs(head)`,
      `view_pr`, `create_draft_pr` (parses the URL, refuses another repository's URL, then
      re-reads with `view_pr`), `mark_ready`, `pr_checks`, `view_release` (`None` only for exit
      1 with `release not found`), `create_release` (`--verify-tag`, never a draft), `upload_assets`
      (draft only; `ForgeError` otherwise), `publish_draft`, and `download_assets`.
    - `list_prs(head)` takes no base. `gh pr list --head` filters by name, and I6 needs to see a
      PR with the right head and the wrong base, so the base check belongs to CP7's identity
      verification, not to a filter. A full page (`--limit 200`) is undecidable, since it may
      be truncated.
    - `pr_checks` follows the plan's exit-code table: JSON with exit 0, 8 or 1 means checks; exit
      1 with `no checks reported` and no JSON means `no_checks`; anything else is undecidable.
      `bucket` must be one of `pass`, `fail`, `pending`, `skipping` or `cancel`.
    - Records are type-checked field by field (`bool` is never accepted as `int`, and OIDs must
      be hex). Anything malformed is `ForgeUndecidableError`, and so is every failed `gh` call
      and every failed mutation. There is no merge, close, delete, edit-body, comment or `api`
      operation.
  - `tests/fake_gh.py` (executable): PRs and releases live in a JSON state file, and branches
    and tags are read from the bare origin through Git. Every argv is logged first. Failure
    injection per `"<group> <sub>"` or `"*"`: `auth` (exit 4), `network`, `server_error`,
    `malformed` and `malformed:<exit>`. It requires `--repo` on every call except `repo view`.
    It models: `pr create` failing with no commit beyond the base and with a duplicate open
    head/base (TBR-R5-001, TBR-R6-002); `pr list --head` by name over closed and merged PRs
    (TBR-R5-002); an open PR's `headRefOid` following the origin; `pr checks` exit codes 0, 8
    and 1, and `no checks reported`; `release create --verify-tag`; and `upload`/`download`
    that never overwrite. It has no `pr merge`. A fixture seeds the state file directly
    (TBR-R6-003).
  - `tests/fixtures.py`: `build_origin_pair(tmp)` (a bare origin plus a clone on `main`) and
    `fake_gh_env(tmp, origin=..., failures=...)` (`PATH` shim and state file).
  - Tests: `tests/test_gitrepo.py` (24), `tests/test_forge.py` (21) and
    `tests/test_no_rewrite_invariants.py` (5), all in the `trunk` CI shard, with `validate.yml`
    regenerated. The static scans read list and tuple argv literals in `controller/*.py`. The Git
    scan checks the plan's rewrite spellings, plus any mutating Git subcommand outside
    `gitrepo.py`. The forge scan checks `pr merge`, `pr close`, `release delete`, `--clobber`
    and `gh api`. A planted module shows that identifiers and prose are not flagged. A
    self-check keeps the scan from passing vacuously. For now, `test_forge` asserts at module
    teardown that no fake `gh` log contains `pr merge`/`pr close`. The suite-level e2e assertion
    comes with CP9.
  - Verified:
    - narrow: `tests.test_gitrepo`, `tests.test_forge`, `tests.test_no_rewrite_invariants`,
      `tests.test_package_structure`, `tests.test_write_containment`, `tests.test_ci_workflows`
      (116 tests), OK;
    - full: `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest discover -s tests -t .`
      ran 1385 tests, OK (6 skipped), in 104 s;
    - `python3 tools/ci_workflows.py --check`: passes.
- **CP4 -- release transaction: complete.**
  - New `controller/release_txn.py`, after `runtime` in the dependency order (it imports
    `errors`, `repo_policy`, `gitrepo`, `forge` and `runtime`). New error
    `ReleaseTransactionError` (`RELEASE_TRANSACTION_REFUSED`). Nothing in it names a trunk, a tag
    prefix, a version file or an artifact kind: all of that comes from the committed policy.
  - `classify(ctx, C)`, the plan's table, first matching row:
    - it refuses when `release.enabled` is off, and when `C` is not an ancestor of the freshly
      fetched `<remote>/<trunk>`;
    - it reads `V` with `read_committed_version` at `C`, fetches `refs/tags/*:refs/tags/*` (never
      forced), lists the remote's tags with the new `gitrepo.ls_remote_tags` (peeled), keeps the
      tags `tag_format` renders canonically from a version, and decides ancestry locally;
    - `M` and the baseline set follow TBR-R3-002/TBR-R4-003. The baseline is read lazily, only
      once every row above `BASELINE_UNRELEASED` has failed, so the forge sees one
      `release view` for the tag plus one per non-acknowledged lower ancestor tag. An
      acknowledged tag is never read;
    - `ALREADY_RELEASED`/`RELEASE_MISMATCH` download the published assets and check asset-set
      consistency: exactly the artifacts' file names plus the checksums file, a checksums file
      listing exactly the other assets with matching SHA-256s, and the policy's `verify` command
      passing each artifact for the target commit. `NO_CHANGE` downloads nothing;
    - the result carries `state`, `version`, `tag`, `commit` (`C`), `target` (the tag's own
      commit when the tag exists, else `C`), `unsettled` and `problems`. `outputs()` gives the CI
      outputs, with `commit` being the target.
    - Classification reads the policy committed at `C` itself, so the commit acknowledging a tag
      carries its own `abandoned_tags`. The plan does not name the revision explicitly; this
      follows "read from a committed tree". A pre-policy commit therefore refuses rather than
      classifying: `main.yml` only ever classifies the trunk tip, and CP9's replay scenario
      builds its own history with a policy.
  - `build(ctx, commit)` requires `HEAD` to be the target commit, runs the policy's `build`
    command there, and requires every artifact path to exist. `verify(ctx, commit)` runs the
    policy's `verify` command on each artifact. Commands run without a shell, stdin closed,
    30-minute timeout, through an injectable command runner.
  - `publish(ctx, C, built_commit, tagger=...)` recomputes the classification (fresh tag fetch,
    fresh forge reads):
    - `ALREADY_RELEASED`/`NO_CHANGE` at the built commit is a verified no-op. Any other
      non-publishing state refuses, and so does a target that is not the built commit;
    - 1: the built artifacts at their policy paths are re-verified for the target;
    - 2 (`RELEASE_DUE` only): `create_annotated_tag` at `C` as `tagger` (the new `tagger`
      parameter passes `-c user.name/-c user.email`), then `push_tag`. A rejected push
      reclassifies **without** fetching tags (our local tag object now differs from any tag
      another run pushed, and the tag fetch would refuse to overwrite it). `RESUME` at `C`
      continues; anything else refuses;
    - 3: `ls-remote` must peel the tag to the target;
    - 4: no release means `create_release` with the artifacts plus a freshly computed checksums
      file. A draft is downloaded: a foreign name, an artifact failing `verify`, or a checksums
      file that disagrees with the final artifact bytes refuses before anything is uploaded.
      Present artifacts are authoritative, missing ones come from this run's build, and a
      missing checksums file is computed over the final bytes. Then `upload_assets` for the
      missing ones and `publish_draft`;
    - 5: `view_release` must show a published release, and its downloaded asset set must be
      consistent for the target.
  - `tools/release.py`, the reference adopter's thin CLI: `version` now prints the policy's
    version-source value at `HEAD`'s committed tree (an absent policy refuses). New `classify
    --commit C` prints `key=value` lines and appends them to `$GITHUB_OUTPUT` when set, and
    exits 1 on a failing state after writing them. New `build` and `verify` act at the checked-out
    commit. New `publish --commit TARGET` classifies `HEAD` and tags as `github-actions[bot]`.
    `verify-wheel` and `checksums` are unchanged. `verify-tag`, `check-unpublished` and
    `verify-tag-commit` stay until CP5 deletes `release.yml`. A `ControllerError` becomes the
    same one-line refusal (`refused: <CODE>: ...`), with newlines collapsed.
  - Tests:
    - `tests/test_release_txn.py` (25): a toy adopter, not this repository. Its `build` writes
      `dist/pkg-<version>.txt` with a random nonce, so a rebuild is not byte-identical, as a
      wheel is not. Its `verify` checks the first line and logs its argv. It runs over a bare
      origin and the fake `gh`. It covers every row, including the v1.1.0 shape, interrupted
      releases (absent and draft), the later-bump, hand-pushed-tag, acknowledged-higher-tag
      (naming two unsettled tags) and resume-commit cases, the `release view` counts, and four
      mismatch variants. Transaction cases: release due (tagger checked, re-publish is a no-op),
      unbuilt/unverifiable artifacts refuse before tagging, a build of another commit refuses,
      interrupted after the tag push (the policy's `verify` ran only for the tag's commit, and
      the tag object is unchanged), the mid-upload draft keeping its artifact, a checksums-only
      draft (disagreeing: untouched; agreeing: filled), foreign/unverifiable drafts untouched,
      and concurrent tags at the same commit (continues) and at another commit (fails). At module
      teardown, no `gh` or Git argv contains `--force*`, `--clobber`, `delete`, `-f` or `-d`;
    - `tests/test_release_tools.py`: `version` through the policy (committed tree, not the
      working tree; missing policy; unreadable version). `CheckedOutVersionTest` now copies the
      reference policy. Plus the `classify`/`build`/`verify`/`publish` CLI end to end on the toy
      adopter, including `$GITHUB_OUTPUT` and the one-line refusals;
    - `tests/test_gitrepo.py`: `ls_remote_tags` and the tagger identity.
  - `test_release_txn` joins the `trunk` CI shard, with `validate.yml` regenerated. `release_txn`
    is added to `controller/__init__.py` and `test_package_structure`'s order.
  - Verified:
    - narrow: the `trunk` and `docs` shards plus `test_package_structure`,
      `test_write_containment` and `test_identity` (367 tests), OK;
    - full: `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest discover -s tests -t .`
      ran 1417 tests, OK (6 skipped), in 110 s;
    - `python3 tools/ci_workflows.py --check`: passes.
- **CP5 -- CI/CD: complete.**
  - `tools/ci_workflows.py` is still the single model, rendered with `--write` and checked with
    `--check`:
    - `ci.yml`: `on: pull_request` only, concurrency `${{ github.workflow }}-${{ github.ref }}`
      with `cancel-in-progress: true`, calling `validate.yml`;
    - `main.yml` (new): `on: push: branches: [main]` and `workflow_dispatch` with no inputs,
      concurrency group `main-release` with `cancel-in-progress: false`, top-level
      `permissions: contents: read`. Jobs:
      - `validate`: `uses: ./.github/workflows/validate.yml`;
      - `release-plan` (needs `validate`, `if: github.ref == 'refs/heads/main'`, job env
        `GH_TOKEN: ${{ github.token }}`, no job permissions, so read-only): a SHA-pinned checkout
        with `fetch-depth: 0`, `fetch-tags: true` and `persist-credentials: false`, then
        `python3 tools/release.py classify --commit "$(git rev-parse "$GITHUB_SHA^{commit}")"`
        (step id `classify`). Its job outputs `state`, `version`, `tag` and `commit` are the
        step outputs `classify` appends to `$GITHUB_OUTPUT`. A failing state fails the job;
      - `build` (needs `validate` and `release-plan`, the same trunk condition and
        `state == 'RELEASE_DUE' || state == 'RESUME'`): checkout of exactly
        `needs.release-plan.outputs.commit` (`fetch-depth: 0`, `persist-credentials: false`),
        then `tools/release.py build`, `tools/release.py verify`, the unchanged pipx smoke test,
        `checksums dist`, and the `dist` artifact upload;
      - `publish` (needs all three, the same condition, the only `contents: write` job, env
        `GH_TOKEN` and `COMMIT: ${{ needs.release-plan.outputs.commit }}`): a credentialed
        checkout of the trunk commit (`fetch-depth: 0`, `fetch-tags: true`, needed to push the
        tag), the artifact download into `dist`, then
        `python3 tools/release.py publish --commit "$COMMIT"`.
    - `validate.yml`: jobs and matrices unchanged. Every checkout now carries
      `persist-credentials: false`, and its header names `ci.yml` and `main.yml` as callers;
    - `release.yml` is deleted. The model lists it in `RETIRED_WORKFLOWS`: `--write` removes it,
      and `--check` fails while it exists, so a hand-pushed `v*` tag cannot trigger a release
      again through a stale file.
  - **Deviation, for the reviewer.** The plan puts `persist-credentials: false` on
    `release-plan`, but `classify` runs `git fetch` (trunk and tags) and `git ls-remote` against
    `origin`, and `RodrigoFAbreu/workflow-controller` is **private** (`gh repo view` reports
    `PRIVATE`). As planned, those reads would fail without credentials on every trunk run. The
    checkout still does not persist its token. Instead, the `classify` step alone carries
    `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=credential.https://github.com.helper` and
    `GIT_CONFIG_VALUE_0=!gh auth git-credential`. Git then gets the job's read-only `GH_TOKEN`
    through `gh`, in that step's environment only, and nothing is written to the checkout's Git
    configuration. `controller/gitrepo.py` inherits the environment, so the helper reaches its
    `git` calls. `gh auth git-credential get` returning `GH_TOKEN` was checked locally with a
    dummy token. No GitHub run was made (nothing is pushed in this milestone), so the end-to-end
    behaviour is first exercised by the post-acceptance 1.2.0 release run.
  - `tools/release.py`: `verify-tag`, `check-unpublished` and `verify-tag-commit` are removed,
    with their helpers (`verify_tag`, `check_unpublished`, `remote_tag_commit`,
    `verify_tag_commit`), the injectable `run_gh`/`run_git` runners of `main()`, and the imports
    only they used. `verify-wheel` stays: it is the reference policy's `verify` command and
    `validate.yml`'s `package` check.
  - Tests:
    - `tests/test_ci_workflows.py` (49), release section rewritten. `ci.yml` is triggered only by
      pull requests and cancels on the same ref. `main.yml` is checked for: its triggers (no
      `workflow_dispatch` inputs, no tags, no pull request); a `main-release` group that never
      cancels; the needs chain; the trunk and `state` conditions on `release-plan`, `build` and
      `publish`; `contents: write` only on `publish`, with a read-only top level;
      `release-plan`'s `GH_TOKEN` and its step-scoped credential helper; eight SHA-pinned
      actions; `classify`'s peeled `--commit` and the four outputs; the build order; `publish`
      calling `tools/release.py publish --commit "$COMMIT"` as its only tool step; and the
      artifact contract against the reference policy's artifact directory. Across all workflows:
      `persist-credentials: false` on every checkout of a job that cannot write, and on none of
      `publish`'s; no step with `--clobber`, `--force`, `delete`, `-f`, `git tag`, `git push`,
      or a direct `gh` call; `release.yml` absent and retired (`--check` fails on it, and
      `--write` removes it). `workflow-conformance.yml` still matches `installation.json`, and
      the PyYAML cross-check parses every rendered file back to its model;
    - `tests/test_release_tools.py`: the `VerifyTagTest`, `CheckUnpublishedTest` and
      `VerifyTagCommitTest` classes are removed. `CheckedOutVersionTest` covers `version` and
      `verify-wheel`. The new `RetiredSubcommandsTest` checks that the parser offers exactly
      `version`, `classify`, `build`, `verify`, `publish`, `verify-wheel` and `checksums`, that
      each retired name exits 2 with `invalid choice`, and that the module no longer defines the
      retired helpers.
  - Not changed here: `README.md`'s "Releasing" section and ADR 0002 still describe the
    tag-triggered `release.yml`. The plan rewrites them in CP10.
  - Verified (narrow): the `docs` and `trunk` shards (292 tests), OK;
    `python3 tools/ci_workflows.py --check` passes.
- **CP6 -- milestone branch binding: complete.**
  - New `controller/milestone_branch.py`, after `release_txn` in the dependency order (it imports
    `errors`, `repo_policy`, `gitrepo`, `forge` and `runtime`). New errors `BranchBindingError`
    (`BRANCH_BINDING_REFUSED`) and `BranchInvariantViolatedError` (`BRANCH_INVARIANT_VIOLATED`).
  - Durable records under `<runtime_root>/repositories/<repo_key>/milestones/`: `<id>.json`, the
    append-only `<id>/events.jsonl` (every line carries `binding_generation`), and retired
    `<id>.abandoned-<n>.json`. `repo_key` is the SHA-256 of the resolved Git common directory.
    `write_record` checks every write against `TRANSITIONS` (the plan's table, row by row; a
    same-state rewrite is always legal) from the state on disk read fresh, and creates a missing
    record with `O_EXCL` semantics. The eleven states are split into `NON_TERMINAL_STATES`,
    `REFUSAL_STATES` and `TERMINAL_STATES`.
  - `preflight(ctx, requested_work_item_id=..., head_policy=...)` returns `Proceed` (with
    `work_item_override`, the governing `binding` and the `action` taken) or `Gate` (a code,
    message and exits), or raises. It follows the plan's resolution order:
    - no binding record and no enabled policy at `HEAD`: `Proceed()`, nothing written (I1);
    - rule 1 (`HEAD` on a record's branch): `BRANCH_PLANNED` is first completed by its
      adopt/collision rows. Terminal records gate `switch_to_trunk`. `PR_CLOSED_UNMERGED` gates
      `pr_closed_unmerged` (CP7 adds the reopen re-read). `MERGED_BEFORE_ACCEPTANCE` gates
      `merged_before_acceptance`, naming `--new-pr` and `--abandon` only when its precondition
      holds, or `--abandon` alone when `<remote>/<trunk>` records the item complete with no
      acceptance commit on `branch_point..H`. A bound item missing from the working tree gates
      `bound_item_missing` (restore, plus `--abandon` when it applies). An explicit or active
      work item that is neither the bound item nor its remediation child refuses, naming both.
      Otherwise the later-preflight checks run (no rewrite against the last observed tip; the
      remote branch absent or an ancestor, the refusal naming GitHub's "Update branch"), then
      sync, then `last_observation` (tip, remote branch, remote trunk, `fresh`, `behind`). After
      acceptance the override is the bound item;
    - rule 3 (`HEAD` on trunk): non-terminal and refusal records block, naming the branch and
      the cell's exits (the missing-branch "restore at the last observed tip" variant, and for a
      plan-stage `BRANCH_BOUND` both `--abandon` and "switch back and restore the stash").
      `BRANCH_PLANNED` records are reconciled. Then a single unbound (or only-`ABANDONED`)
      top-level non-terminal item is the bind candidate, two or more refuse, and none runs the
      trunk-start preflight (clean tracked tree, local trunk equal to the fetched remote trunk;
      behind gates `fast_forward_trunk`, ahead or diverged refuses);
    - rule 2 (`HEAD` on an unbound branch that inverts `branch_format`): adopt, with every adopt
      precondition, and the snapshot and branch point `P = merge-base(<remote>/<trunk>, HEAD)`;
    - rule 4: anything else refuses.
  - The bind step checks the plan-stage phase set (`PLAN_STAGE_PHASES`, `AMENDING_PLAN`
    excluded), `plan_approval == null`, `base_commit` reachable from `T`, and no local or
    remote branch (the collision rows, or the `git branch -d` / `git push <remote> --delete`
    exits for an abandoned id). It renames an `ABANDONED` record aside, writes `BRANCH_PLANNED`
    (generation `1 + #abandoned`), runs `git switch -c`, verifies `HEAD`, and writes
    `BRANCH_BOUND`. The `BRANCH_PLANNED` rows run in table order: forge/common-dir mismatch,
    re-bind at `T` or a descendant trunk tip (rewriting `T` and re-reading the snapshot),
    complete from the branch at a descendant of `T`, switch at the same commit, the
    branch-not-descending row (exit (b)), and the catch-all with exits (a) and (c). Exit (c)
    names `--abandon` only when its precondition holds, and create-and-switch otherwise.
  - `find_acceptance_commit` implements CP7's acceptance-commit definition (first-parent, first
    `MILESTONE_COMPLETE` transition, `Workflow-Work-Item` trailer required, an untrailered one
    named). CP6 already needs it for adopt after acceptance and for common check 5.
  - `acknowledge(ctx, id, "new-pr" | "abandon")` performs the common checks in order (record
    state, `HEAD` on trunk or the bound branch and not checked out in another worktree, a fresh
    `view_pr` for a recorded PR, the `--abandon` preconditions of all three shapes, check 5),
    then appends `acknowledged` and writes the new state. CP8 adds the CLI subcommand.
  - **Deviations, for the reviewer.**
    - `runtime.py` gains `create_json` (the `O_EXCL` create the plan requires, as an `fsync`ed
      temp file hard-linked onto the name), `rename_exclusive` (link, then unlink the source:
      never replaces a destination) and `remove_file`, not only a constant. An interrupted
      rename (both names holding the same bytes) is completed rather than repeated, so the
      generation count stays right.
    - `gitrepo.py` gains `switch_at_head`: the `BRANCH_PLANNED` "switch to it" row must carry
      the uncommitted plan, and `gitrepo.switch` requires a clean tree. It refuses unless the
      branch is at exactly `HEAD`'s commit (I4).
    - The record's `repository` block also stores `remote_url`. The forge/common-dir row compares
      the `OWNER/NAME` parsed from a GitHub remote URL with the recorded forge repository. For
      any other URL (the disposable bare origins), it compares the URL itself.
    - Adopt inverts the branch name with the policy at `P` when that one is enabled and
      admissible, so a policy edited on the branch never decides the candidate.
  - Deferred to CP7 by the plan: PR creation and discovery, the reopen re-read, readiness,
    close-out (from the branch, from trunk, PR-less) and the trunk-side reconciliations of
    `PR_PLANNED`/`PR_OPEN`/`READY`/`MERGED`, which block from trunk for now. The gate texts move
    into `decision.py` in CP7.
  - Tests: `tests/test_milestone_branch.py` (64), in the `trunk` shard (`validate.yml`
    regenerated). They cover the CP6 list: fresh bind with the plan carried over, every
    adopt/collision row, crashes at each persist boundary, rewrite and remote-divergence
    refusals, sync, snapshot and branch-point policy, adopt mid-implementation and after
    acceptance, the plan-stage phase set, the catch-all and exits (a), (b) and (c), the
    discarded-plan window and every `--abandon` refusal, re-planning an abandoned id (including
    a crash between rename and create), remediation children, trunk blocking per state, the
    state classification, and the pinned transition table.
  - Verified (narrow): `tests.test_milestone_branch` plus the `trunk` shard,
    `test_package_structure`, `test_write_containment`, `test_ci_workflows`,
    `test_plan_document_consistency`, `test_runtime` and `test_identity` (367 tests), OK;
    `python3 tools/ci_workflows.py --check` passes.
- **CP7 -- Draft PR lifecycle and completion: complete.**
  - `controller/milestone_branch.py` gains the plan's CP7 writers. Rule 1 now runs the branch
    column of the outcome matrix state after state (`_branch_cells`) until a cell returns:
    - `BRANCH_BOUND`: the PR creation condition is "the local tip is not an ancestor of the
      fetched `<remote>/<trunk>`". When it holds, the branch is pushed (an intent, then an
      `ls_remote` re-read), `PR_PLANNED` is written, and discovery runs. When it does not, the
      worker proceeds, unless the tip's committed state is `MILESTONE_COMPLETE`, in which case
      the PR-less close-out applies;
    - `PR_PLANNED`: `discover()` filters `list_prs` to this binding's identity (head, base,
      repository from the PR URL, not cross-repository) minus the excluded set (the live
      `superseded_prs`, plus each `<id>.abandoned-<n>.json`'s `pr` and `superseded_prs`). The
      first matching row decides: excluded-open, 2+ open and 2+ merged refuse with their own
      exits; 1 merged goes to the merged-PR handling; 1 open is adopted (closed matches go to
      `superseded_prs`); closed-unmerged writes `PR_CLOSED_UNMERGED` with the highest number;
      0 matches re-checks the condition (the PR-less close-out, or back to `BRANCH_BOUND`), else
      `create_draft_pr` (title the id, body naming the plan path and the marker line), verified,
      then `PR_OPEN`;
    - `PR_OPEN`/`READY`: a fresh `view_pr` re-verifies I6 first. Closed becomes
      `PR_CLOSED_UNMERGED`; merged goes to the merged-PR handling; a human-set non-draft is kept.
      Open `PR_OPEN` proceeds, or runs readiness once the branch's committed state is
      `MILESTONE_COMPLETE`. `READY` returns the merge gate;
    - readiness: a clean tree (`dirty_tree`); the tip exactly the acceptance commit `A`
      (`post_acceptance_commits`; an untrailered completion refuses); the remote branch and the
      PR head at `A` (`pr_head_not_accepted`); `<remote>/<trunk>` an ancestor of `A`
      (`integration_required`, with the drift count and the manual "Create a merge commit"
      procedure); green checks when the snapshot requires them (`checks_failing`, then
      `checks_pending` including no checks reported, then `checks_cancelled`). Then
      `gh pr ready` (skipped when already ready), a re-read, `READY` with `accepted_head`, and
      the merge gate;
    - `MERGED`: close-out step 3. The tracked tree must be clean (`dirty_tree`). The tip must be
      `merged_head`: `unmerged_commits` names the commits beyond it, and the exit is to move
      them off. Then the Controller switches to trunk, fast-forwards it, writes `CLOSED`, and
      continues to the trunk start (`action == "closed_out"`);
    - the reopen re-read of `PR_CLOSED_UNMERGED` runs before the `bound_item_missing` case, so a
      reopened PR with the bound item gone writes `PR_OPEN` and then gates.
  - `_merged_handling` implements merged-PR handling steps 1 and 2, with the PR-less close-out
    as the `pr is None` case. If `H` is missing locally, it is fetched from
    `refs/pull/<n>/head`. With no acceptance commit on `branch_point..H`, it writes
    `MERGED_BEFORE_ACCEPTANCE` and records `untrailered_completion`, which the gate and the
    trunk refusal name. When `H` is not on the fetched `<remote>/<trunk>`, it writes
    `MERGED_REWRITTEN` with `rewrite_gate_shown: false`. Otherwise it writes `MERGED` with
    `merged_head` and `accepted_head`. The one-time `merge_method_rewrote_history` gate flips
    the flag, from either side.
  - Rule 3.1 now reconciles instead of refusing outright:
    - close-out from trunk (step 0, the other-worktree check; step 1, `view_pr`, where open
      refuses naming the branch and closed writes `PR_CLOSED_UNMERGED` and refuses; step 2, the
      merged-PR handling; step 3, `CLOSED` without a switch, or `unmerged_commits`, with an
      extra exit to delete the local branch);
    - the PR-less form for `BRANCH_BOUND`;
    - `PR_PLANNED` discovery read-only, row by row. Nothing is ever created from trunk.
  - `decision.py` gains `BRANCH_GATE_TEXTS` (one text per `milestone_branch.GATE_CODES` entry,
    pinned equal by a test) and `branch_human_gate()`, which turns a preflight `Gate` into a
    `HumanGate` with phase `MILESTONE_BRANCH`. `SELECTED_COMMANDS` and the dispatch table are
    unchanged. CP8 wires the call.
  - **Deviations, for the reviewer.**
    - Two gate codes the plan does not name: `dirty_tree` (readiness condition 2 and close-out's
      "a dirty tree gates") and `pr_head_not_accepted` (readiness conditions 4 and 5: the pushed
      branch or the PR does not show `A` yet). The plan says a failed readiness condition is a
      gate, not an error, but gives these two no name.
    - While a record is `READY`, the preflight does not push. A local commit after `A` would
      otherwise move a ready PR's head past the accepted head, against I7. The merge gate names
      the unpushed commits instead.
    - The CP6 tests' stub forge is replaced by the executable fake `gh`. An approval commit now
      leads to PR creation, so the CP6 assertions that expected a record to stay `BRANCH_BOUND`
      past the first branch commit, or a bare `Proceed` after acceptance, now assert the CP7
      continuation (PR creation, the checks gate) with the same intent. `MERGED_REWRITTEN`
      fixtures seed the post-gate flag. Two rebind fixtures push the moved trunk, as a real
      moved trunk would be.
  - Tests: new `tests/test_pull_request_lifecycle.py` (89), in the `trunk` shard
    (`validate.yml` regenerated):
    - the outcome matrix is pinned: 36 cells, from the branch, from trunk, and from trunk with
      the branch gone. Shape tests assert that every state has a row from each side, that the
      `BRANCH_BOUND` split and the `PR_PLANNED` discovery rows partition their state, and that
      every cell has a fixture;
    - PR creation, discovery and reuse: creation only after the first branch commit, the
      create/record-write crash, duplicates, identity changes, a reopened superseded PR, and a
      re-bind through to the first PR (including the rename/create crash);
    - readiness: every gate and the checks precedence, `gh pr ready` exactly once, the merge
      gate without a merge call, post-acceptance and untrailered cases, and a child acceptance;
    - close-out: from the branch, from trunk, and PR-less, plus the squash, dirty-tree,
      unmerged-commit and second-worktree cases, and `H` fetched through `refs/pull/<n>/head`;
    - every refusal-state exit, including the spies showing that neither disposition pushes,
      switches or mutates a PR.
    Mutating five rules (the post-acceptance gate, PR exclusion, rewrite detection, I6 identity,
    the drift gate) each fails the module.
  - Verified (narrow): the `trunk` shard plus `test_decision`, `test_golden_plan_stage_decisions`,
    `test_package_structure`, `test_write_containment`, `test_ci_workflows` and
    `test_plan_document_consistency` (475 tests), OK; `python3 tools/ci_workflows.py --check`
    passes.
- **CP8 -- lifecycle wiring and observation: complete.**
  - `job._execute_step_locked` gains step 1b after the state read and before `decide`, under
    the lifecycle lock: `milestone_branch.repository_preflight`.
    - It runs the no-policy `probe` first: `rev-parse --path-format=absolute --git-common-dir`,
      then `ls-tree -z HEAD -- .workflow-controller/policy.json` (`gitrepo.head_tree_has`). On an
      unborn `HEAD` it adds `rev-parse --verify -q HEAD` and `symbolic-ref -q HEAD`. Any other
      result refuses (I9).
    - With no binding record and nothing listed, the result is `Proceed()` and nothing else
      runs. Otherwise `preflight` runs. A policy committed at `HEAD` but deleted in the worktree
      stays active.
    - A `Gate` becomes a `GATE_BLOCKED` record through `_no_launch_record` (exit 10). Its
      `HumanGate` comes from `decision.branch_human_gate` (phase `MILESTONE_BRANCH`). The record
      carries the gate's work item and its Workflow phase, or `NO_PHASE` when the state lacks it.
    - A refusal raises (exit 20). After a preflight that did anything, the state is read again,
      because a bind or close-out may have moved `HEAD`. `work_item_override` replaces the
      selection.
  - Under an active binding (`Proceed.binding`):
    - the job record gains `branch_binding` (work item, branch, pre-step tip);
    - the worker's `--disallowedTools` is `routing.worker_disallowed_tools`, the route's own list
      plus the pinned `routing.BRANCH_GUARD_TOOLS`. `git merge`/`branch`/`tag` are absent, and
      the matching semantics are documented in `routing` and `worker`;
    - before a job can be `FINISHED`, `milestone_branch.verify_post_step` checks that `HEAD` is
      still attached to the bound branch and that the tip descends from the pre-step tip. A
      violation, or an undecidable read, is `FAILED` with the new
      `reconciliation_evidence.code` `BranchInvariantViolated`. The Controller never repairs it.
  - Observation (I10), from local Git and the records only, with no fetch and no `gh`. Every new
    key and line is omitted when the probe finds nothing:
    - `inspect` gains `repository_policy` (path, source `binding` or `HEAD`, SHA-256, both
      switches, trunk, forge repository) and `milestone_branch` (state, branch, branch point,
      generation, PR number/URL/draft, `last_observation`), each with a text line;
    - `explain` gains `repository_preflight` (`milestone_branch.predict`). Its `action` is one of
      `bind`, `adopt`, `complete_binding`, `push`, `create_pr`, `ready`, `close_out`, `gate`,
      `refuse` or `proceed`. It also carries the work item, branch, binding state, gate code,
      detail, and `as_of` (the last observation) for anything network-dependent. The text form
      is one `repository preflight:` line;
    - `status` prints one `milestone:` line per live binding record in the runtime root;
    - `follow` is untouched.
  - `milestone-binding` subcommand:
    `workflow-controller --work-item <id> milestone-binding --new-pr|--abandon <repo>`. The
    disposition group is required and exclusive, and a missing `--work-item` is a usage error
    (exit 2). It dispatches as a mutating command, then takes the lifecycle lock
    (`job.acknowledge_milestone_binding`) and calls `milestone_branch.acknowledge`. The exit is
    0, or 20 with nothing written. It writes no job record and launches no worker.
    `cli.ALL_COMMANDS` and the consistency test's `_COMMAND_NAMES` include it, the
    `extract_invocation_lines` docstring says "eight", and a new test parses this plan's
    `milestone-binding` lines.
  - **Deviations, for the reviewer.**
    - The post-step verification also runs at `resume`. A `LAUNCHED` or `COMPLETED` record with
      `branch_binding` whose transition verifies is `FAILED` (`BranchInvariantViolated`) instead
      of `FINISHED` when the branch invariant fails. The plan says "before the job is marked
      `FINISHED`", and `resume` also marks jobs `FINISHED`.
    - The job record's `branch_binding` block is additive. The plan names no field for the
      pre-step tip, but `resume` needs it after the process that captured it is gone. It is
      absent with no binding (I1).
    - `explain`'s action vocabulary and the `inspect` field names above are this checkpoint's
      choice. The plan names the categories, not the exact keys.
    - `tests/fake_claude.py` gains a `git` scripted action (`fixtures.script_git`), so a test
      worker can switch or rewrite the branch.
  - Tests:
    - new `tests/test_trunk_preflight.py` (29), in the `trunk` shard (`validate.yml`
      regenerated);
    - the no-policy golden `tests/golden/no_policy_lifecycle.json` was generated by
      `tests/golden/generate_no_policy_lifecycle.py` from the unchanged CP7 tree (`86801f6`,
      extracted with `git archive`). It holds job records, worker argv, `inspect`/`explain` JSON
      and exit codes for three scenarios: the full `"2.2"` implementation run to the manual gate,
      the bootstrap `step`, and an unborn-`HEAD` `step`. It re-derives byte-identically from the
      CP8 code, with no new key;
    - the rest: the exact probe argv (two, or four when unborn), the undecidable listing, the
      deleted-in-worktree policy, the inadmissible policy, and the pinned guard list and its
      union. Then, end to end through `cli.main` on a policy-enabled origin/clone with the fake
      `gh`:
      - the bind step's worker argv;
      - the wrong-branch refusal;
      - unrelated item versus bound item and remediation child;
      - a verifying worker that keeps the branch, one that switches it, and one that rewrites it;
      - the same verification at `resume`;
      - the `inspect`/`explain` blocks on trunk and on the branch;
      - `explain` without fetch or `gh` (a git spy and the `gh` log);
      - the `status` line;
      - `follow` output equal, after normalisation, to that of the same step on a no-policy
        target;
      - every `milestone-binding` case in the CP8 list. These are the parser, `--abandon` after a
        closed PR, `--new-pr` after a merge before acceptance with the new Draft PR one branch
        commit later, and the PR-less close-out from the branch and from trunk. They also cover
        the discarded plan after a bind, a refused disposition, and no record with the ordinary
        dispatch footprint;
    - `test_worker` pins the widened list as one argv element;
    - mutating the tool union, the post-step verification, the probe short-circuit, the
      `branch_binding` write, or the omit-never-`null` rule each fails the module.
  - Verified:
    - narrow: `tests.test_trunk_preflight`, `test_worker`, `test_routing`,
      `test_plan_document_consistency`, `test_write_containment`, `test_package_structure`,
      `test_gitrepo` and `test_ci_workflows`, OK; `python3 tools/ci_workflows.py --check` passes;
    - full: `python3 -m unittest discover -s tests -t .` ran 1593 tests, OK (6 skipped), in
      152 s.
