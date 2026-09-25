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
