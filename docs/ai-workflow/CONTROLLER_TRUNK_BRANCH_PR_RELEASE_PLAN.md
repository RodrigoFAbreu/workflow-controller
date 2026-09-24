# Controller trunk-based branch, pull request and release orchestration (Revision 10)

Work item: `workflow-controller-trunk-branch-pr-release-orchestration`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `00c3b7136dae2ca1993af91c999df48ad376f433` ("Prepare the 1.1.1 patch release"). The
previous milestone's acceptance commit is `82fa6a8`, but two commits follow it on `main`: the
test-isolation fix `5832b16` and the 1.1.1 release preparation `00c3b71`. Neither is this
milestone's work, and using `82fa6a8` as the base would put both into this milestone's reviewed
diff. The base is therefore the commit this milestone actually starts from.
Lifecycle authority: installed Workflow 2.5.1 (`.claude/commands/`, `scripts/workflow_state.py`,
`scripts/workflow_fingerprint.py`, `scripts/prepare-ai-review.sh`) and each work item's own
`governing_workflow_version`.
Stable orchestrator while this milestone runs: the pipx-installed release Controller 1.1.1.

## Goal

Establish a reusable, lightweight trunk-based development and release model, with
`workflow-controller` as its first adopter:

```text
main ── create milestone/<work-item-id> ── Workflow milestone on that branch ── Draft PR to main
     ── plan / implementation / reviews / functional review ── trunk-freshness check
     ── MILESTONE_COMPLETE ── PR marked ready ── HUMAN merges ── CI validates main
     ── version changed? publish release : normal CI only
```

Concretely:

1. **Generic capability** (reusable by any Workflow-managed repository): milestone branch
   binding, a Controller-owned GitHub boundary, Draft PR lifecycle, trunk-drift detection, a
   repository release policy, and a crash-safe release transaction with explicit collision and
   recovery states.
2. **Reference configuration** (this repository only): trunk `main`, branches
   `milestone/<work-item-id>`, version source `pyproject.toml`, tag `v{version}`, artifacts =
   Python wheel + `SHA256SUMS`, published as a GitHub Release.
3. **One version authority**: `pyproject.toml`'s static `[project].version`. The package runtime
   reads its version from its own distribution metadata. The source runtime reads it from its own
   code root's `pyproject.toml`, and never from `importlib.metadata`.
4. **Release on version change**: a push to `main` always runs validation. It releases exactly
   when the authoritative version is new relative to the last release tag in its history. The tag
   is created only after validation and artifact verification, at the exact validated commit, and
   is never moved.
5. **Workflow stays authoritative**: under Workflow 2.5.1 the Controller detects trunk drift and
   fails closed at readiness. It never rewrites Workflow review identity. This milestone ships and
   is accepted entirely under the installed Workflow 2.5.1, and CP10 is its terminal checkpoint.
   Binding the branch/base contract to the released Workflow 2.6.x is a separate follow-up
   integration milestone, started only after both this milestone and the parallel Workflow
   Manager 2.6 milestone are complete (user decision; "Follow-up milestone boundary" below).

## Non-goals

Carried verbatim from the milestone scope:
- a permanent `develop` branch;
- auto-merging PRs;
- GitHub branch protection as a requirement, or any paid branch-protection feature;
- the future web UI;
- broad multi-worktree scheduling/concurrency;
- adapters for every ecosystem;
- redesigning Workflow lifecycle semantics inside the Controller;
- modifying frozen Workflow releases;
- adopting unreleased Workflow 2.6 implementation details;
- upgrading this repository's installed Workflow to 2.6.x, or binding any Controller behaviour
  to the released 2.6.x contract. Both belong to the follow-up integration milestone (see
  "Follow-up milestone boundary"), whose scope is measured from the actual release, not guessed
  here;
- roadmap §1.4 polish (resume hints, manual-external gate ledger coherence, relaunch-bound
  tests, active-job presentation). None of it is needed here.

Also out of scope:
- any edit to `scripts/`, `.claude/commands/` or `.github/workflows/workflow-conformance.yml`.
  These are managed Workflow release content, and this milestone makes no Workflow Manager
  upgrade;
- deleting branches, locally or on the remote. GitHub's "automatically delete head branches"
  setting is the operator's choice;
- editing a PR's title or body after creation, labels, reviewers, assignees, or PR comments;
- publishing to a package index;
- executing this milestone on a milestone branch. `CLAUDE.md` forbids pushing and opening PRs,
  and the machinery does not exist until it is released. This milestone runs on `main` as every
  previous one did, driven by Controller 1.1.1, and the first dogfooded milestone is the next one;
- bumping the version inside this milestone. The first automatic release (1.2.0) is a
  post-acceptance release-preparation commit, as `00c3b71` was for 1.1.1 (see "This milestone's
  own rollout").

## Investigation

### Version machinery today

- `controller/version.py` holds `__version__ = "1.1.1"`. `pyproject.toml` declares
  `dynamic = ["version"]` with `[tool.setuptools.dynamic] version = {attr =
  "controller.version.__version__"}`.
- Readers of `version.__version__`:
  - `controller/identity.py`: the `ControllerIdentity.version` default (:90), `resolve_runtime`'s
    `validate_build_info(expected_version=...)` (:295), and `materialise`'s pin body and runtime
    block (:625, :635);
  - `controller/cli.py`: `version_text` (:204);
  - `tools/release.py`: `version`, `verify-tag` and `verify-wheel` (:324);
  - `setup.py`: the `build_py` hook loads `controller/version.py` by file path (:78);
  - tests: `test_handoff`, `test_buildinfo`, `test_identity`, `test_packaged_runtime`,
    `test_cli`, `test_release_tools` and `fixtures.build_package_tree`.
- `controller/handoff.py` reports the origin's approved version by regex-parsing
  `HEAD:controller/version.py` (:41, :120-128). A package runtime reads it from the installed
  `BUILD_INFO.json` (:131-154).
- `tools/release.py verify-tag` requires the *dynamic* attr form. `test_buildinfo.py:55-60` and
  `test_release_tools.py:100-117` pin that form, and `test_packaged_runtime._write_version`
  rewrites `controller/version.py` to simulate upgrades (cases 4 and 8).
- The source snapshot pathspec already includes `pyproject.toml`
  (`identity._SNAPSHOT_FILES`), so a pinned source child can read its version from its own
  snapshot.
- The hazard behind "must not report the pipx version": this checkout holds an untracked
  `workflow_controller.egg-info/` from an old editable install, and `python3 -P -m controller`
  runs with `PYTHONPATH=$C`. `importlib.metadata.version("workflow-controller")` would resolve
  whichever distribution is first on `sys.path`, and that can be stale or unrelated. The source
  runtime must therefore never consult distribution metadata.

### Release pipeline today, and the v1.1.0 lesson

- `release.yml` triggers on a pushed `v*` tag: validate, then build (verify-tag, peel,
  on-main, wheel, verify-wheel, pipx smoke, checksums), then publish (check-unpublished,
  re-verify, verify-tag-commit, `gh release create --verify-tag`). Only `publish` has
  `contents: write`, and every action in it is SHA-pinned. `ci.yml` and `validate.yml` keep major
  tags, a deliberate ADR 0002 decision.
- v1.1.0: a human pushed the tag. Validation then failed in the `decision` shard on a
  test-isolation defect (`5832b16`), before build or publish. The tag stayed. Tags are never
  moved, so the release shipped as 1.1.1 (`00c3b71`), and `v1.1.0` remains an annotated tag at
  `82fa6a8` with no release. **Lesson: a tag must never exist before validation succeeds.** The
  tag-first trigger is structurally wrong: the tag is the input, not the output.
- A tag or release created with `GITHUB_TOKEN` does not trigger other workflows. A design where
  a main-branch workflow pushes a tag to start `release.yml` would silently never release. The
  release must therefore be one main-branch transaction.

### The Controller's Git and GitHub footprint today

- Every Git call in `controller/` is read-only: `rev-parse`, `show`, `log`,
  `merge-base --is-ancestor`, `interpret-trailers`, `hash-object` without `-w`, `ls-files`. The
  only exception is `git archive` on the Controller's *own* origin into the runtime `source/`
  directory. Nothing runs `commit`, `switch`, `branch`, `push` or `fetch`, and nothing calls `gh`.
  The only code that mutates the target repository is the launched Claude worker.
- Workers run `claude -p ... --permission-mode auto`. The only `--disallowedTools` are
  `Agent,Workflow,Skill` on single-agent routes (`routing.SUBAGENT_TOOLS`). There is no
  restriction on `git push`, `gh`, merges or branch switches.
- `tests/test_write_containment.py` constrains file writes under the runtime root. It
  explicitly does not see subprocess writes, so target-repository Git mutation needs its own
  static guard.
- The runtime root is global: ladder rows `--runtime-dir`, `$WORKFLOW_CONTROLLER_HOME`,
  `<origin checkout>/.controller` (source runtime only), then XDG state. Job and run records key
  a target by `target_repo = str(managed_repo.root)`. The lifecycle lock is per worktree, taken on
  `git rev-parse --absolute-git-dir` inside `job.execute_step` (`job.py:3513`).
- `decision.decide_no_work_item` always selects bare `/milestone-plan`, and
  `_decide_milestone_complete` returns "terminal: nothing to do". Neither knows about branches.
- Exit codes are a closed set tied to ADR 0001 by `test_plan_document_consistency`. No new exit
  code is needed: gates use `EXIT_GATE` (10), refusals use `EXIT_FAIL_CLOSED` (20).

### Workflow 2.5.1 facts that decide the branch/base boundary

Measured in the installed `scripts/`:

1. **The plan stage makes no commit.** `/milestone-plan`, the reviews and `/apply-plan-review`
   leave their output uncommitted. The first commit of a milestone is the user-only
   `/approve-review plan` approval commit (`git log 16c3fb4..3397e39`). So there is a window
   where the work item exists (its id is known) and nothing is committed: a branch can be created
   at the trunk tip with `git switch -c` and the uncommitted plan files carried over unchanged.
2. **The work-item id is derived by `/milestone-plan`, not supplied to it.** A bare
   `/milestone-plan` derives the id, and `/milestone-plan <id>` refuses an id that is not already
   a `work_items` key. The Controller cannot name the branch before the work item exists.
3. **`base_commit` is fixed at `route_work_item`.** A later different value is refused
   (`WorkItemDeclarationFactConflictError`). The implementation bundle diff is
   `git diff <base_commit>` (`prepare-ai-review.sh:386-405`), so merging a newer trunk into the
   branch would put unrelated trunk changes into every later implementation diff.
4. **Implementation provenance intervals refuse merge commits.**
   `_classify_generation_record_interval` raises `NonFirstParentProvenanceIntervalError` for
   any commit with more than one parent in `reviewed_implementation_head..T`. Evidence
   discovery is first-parent (`discover_approval_commits`). An integration merge inside a
   provenance interval makes Workflow fail closed.
5. **Implementation-stage `review_content_id` hashes protected content.** An integration that
   changes a protected path stales `technical_approval` through Workflow's own check. One that
   changes only excluded paths does not. It still changes the commit that was tested.
6. **There is no Workflow transition that re-bases a work item or re-opens implementation
   review because trunk moved.** `/apply-functional-review`'s bounded branch
   (`mark_technical_approval_stale`) exists only for functional findings.
7. **`WORKFLOW_STATE.json` is one file on every branch.** Two concurrent milestone branches
   each add their own `work_items` entry and both edit `docs/ACTIVE_MILESTONE.md`, so the second
   merge to `main` conflicts. That is Workflow's state model, not the Controller's.

### Test and tooling constraints

- `tools/ci_workflows.py` shard coverage: every `tests/test_*.py` module must appear exactly
  once. New modules go into a new `trunk` shard.
- `test_package_structure`: `controller.__all__` equals the module set, in `DEPENDENCY_ORDER`,
  and a module imports only earlier modules. `buildinfo`, `version` and `errors` import nothing
  from `controller`.
- `test_plan_document_consistency`: every backticked `workflow-controller <cmd>` span in the
  README and ADRs must parse under `cli.build_parser()`, and the README's validate-job list must
  equal `["controller", "conformance", "package"]`. This plan adds no validate job.
- The frozen Workflow conformance suites run from `scripts/` and are untouched by this
  milestone.

### Baseline test state

To be recorded at CP1 start, as previous milestones did: `python3 -m unittest discover -s tests
-t .` (with no `PYTHONPATH=.`, see memory "Controller tests: no PYTHONPATH=."), the seven
conformance suites, and `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest
tests.test_packaged_runtime`. The known `test_worker` hanging-worker reap-check flake under load
is re-run once before it is counted as a failure.

## The Workflow / Controller boundary

**Workflow owns** lifecycle state, review identity (`review_content_id`, approvals, provenance
intervals, `base_commit`) and every answer to "is this reviewed result still the reviewed
result?". **The Controller owns** Git refs it creates, the GitHub PR and release objects, the
repository policy, and its own durable binding records.

| Capability | Under Workflow 2.5.1 (this milestone, CP1-CP10) | Waits for released Workflow 2.6.x (follow-up integration milestone) |
| --- | --- | --- |
| Create/adopt `milestone/<id>` at the trunk tip | yes, in the uncommitted plan-stage window (fact 1) | -- |
| Draft PR create/reuse/verify, branch sync | yes | -- |
| Detect trunk drift | yes, read-only | -- |
| Refuse readiness when drifted | yes, fail-closed gate | -- |
| Integration merge primitive (`git merge --no-ff <remote>/<trunk>`) | implemented and tested in disposable repos only, never invoked by the lifecycle | wired to the Workflow transition that re-establishes base and review identity |
| Re-establish review/test identity after integration | **not attempted**: facts 3-6 show any Controller attempt would either pollute diffs or break provenance | bound to whatever the released contract defines |
| Branch-aware Workflow state (recorded branch/base, concurrent-branch state merging) | Controller records its own binding only, not in Workflow state | consumed from Workflow if released |
| Release policy and transaction | yes, independent of Workflow | -- |

**What the Controller needs from Workflow 2.6.x.** These are questions for the follow-up
integration milestone to answer from the released contract, not fields to invent now. This
milestone records them only so that the boundary is explicit:

- **E1.** Does Workflow record a work item's branch and/or integration base? If it does, the
  Controller binding must agree with it and defer to it.
- **E2.** Is there a legal transition that moves a work item's base (`base_commit` or its
  successor) to a new trunk tip after an integration merge? Which approvals does it stale, and
  which review stage does it re-enter?
- **E3.** Do provenance intervals accept a history-preserving integration merge (fact 4), and
  under what trailer or record?
- **E4.** In which phases is integration legal?
- **E5.** How do `WORKFLOW_STATE.json` and the per-item narrative documents merge when two
  milestone branches land (fact 7)?

If 2.6.x answers E2-E4, the follow-up milestone binds the integration primitive to that
transition. If it does not, the follow-up records the narrow Workflow change instead. Until then,
and in this milestone's accepted state, the 2.5.1 fail-closed `integration_required` readiness
gate is the Controller's contract, and the Controller never grows a competing base/review model.

## Invariants

- **I1 No activation, no change.** A repository without `.workflow-controller/policy.json` at
  `HEAD`, and with no binding record, behaves exactly as under Controller 1.1.1: identical
  decisions, job records, worker argv, CLI output (including every `inspect`/`explain` JSON
  document, which gains no key, not even a `null` one) and exit codes. The only added cost is a
  fixed budget of two read-only `git` calls, which the policy probe needs, or four on an unborn
  `HEAD` (see "Lifecycle wiring"). A present but inadmissible policy (unknown schema, unknown keys,
  unknown adapter kind, invalid values) refuses every lifecycle command, fail-closed. It is never
  ignored.
- **I2 Never rewrite.** The Controller never force-pushes, `reset`s, rebases, amends,
  deletes a ref, or moves an existing tag. Every push is a plain refspec push. For a branch it
  must be a fast-forward, checked locally first. A tag push must create a new ref.
- **I3 Never merge to trunk.** No Controller code path merges a PR or pushes to the trunk ref.
  The forge boundary has no merge operation, and a static test forbids the spellings. A human
  merges every PR.
- **I4 Never switch implicitly.** The Controller changes `HEAD` in exactly three places: the
  bind step (`git switch -c` to a new branch at `HEAD`'s own commit), adopting a
  crash-interrupted bind whose branch sits at `HEAD`'s commit, and close-out after a *verified
  merged* PR (switch to trunk, clean tree only). Anything else that finds `HEAD` elsewhere
  refuses.
- **I5 Persist before act.** Every Git or GitHub mutation is preceded by a durable intent record
  and followed by a verified outcome record. Restart reconciles from the live refs/PRs against the
  record and never repeats a mutation blindly.
- **I6 Identity is verified, not assumed.** A PR is ours only if its number, head ref, base ref,
  repository and non-cross-repository flag all match. Zero matches means create; exactly one
  open match means adopt; anything else refuses.
- **I7 Drift is never equivalence, and ready means accepted.** A PR is marked ready only if its
  head is exactly the work item's *acceptance commit*. That is the first-parent commit on the
  branch whose committed `WORKFLOW_STATE.json` first records the work item as
  `MILESTONE_COMPLETE`, cross-checked by its `Workflow-Work-Item: <id>` trailer. The remote trunk
  tip, freshly fetched, must also be an ancestor of that commit.
- **I8 Tag after validation.** A release tag is created only after the complete validation matrix
  and artifact verification for that exact commit succeed. It points at that commit and is never
  moved or deleted.
- **I9 Undecidable is refusal.** Any `gh`/`git` read whose result cannot be classified (auth,
  network, malformed output) refuses. It is never treated as "absent".
- **I10 Presentation is derived.** `inspect`/`explain`/`status` render durable records and local
  Git reads. They never mutate, fetch or call `gh`.

## Design

### A. Generic capability vs B. reference configuration

| Generic (`controller/`, ships in the wheel) | Reference configuration (`.workflow-controller/policy.json`, this repo) |
| --- | --- |
| `repo_policy.py`: schema, validation, closed adapter registries | trunk `main`, remote `origin`, forge `github` `RodrigoFAbreu/workflow-controller` |
| `gitrepo.py`: target-repository Git plumbing | branch format `milestone/{work_item_id}` |
| `forge.py`: the GitHub boundary via `gh` | version source `pyproject` at `pyproject.toml`, scheme `semver` |
| `milestone_branch.py`: binding, PR lifecycle, drift, readiness, close-out | tag format `v{version}` |
| `release_txn.py`: classification, tag/publish/resume transaction | build/verify commands: `pip wheel`, `tools/release.py verify-wheel` |
| | artifacts: `dist/workflow_controller-{version}-py3-none-any.whl` + `SHA256SUMS`, GitHub Release |

Nothing in column A names `main`, `v`, `pyproject.toml`, wheels or this repository. The
Controller-specific wheel checks (BUILD_INFO, package digest, entry point) stay in
`tools/release.py verify-wheel` and are reached only through the policy's `verify` command. A
Gradle or Node adopter would add a version-source kind and point `build`/`verify` at its own
commands, without touching column A's orchestration.

### Repository policy (`controller/repo_policy.py`, CP2)

Location: `.workflow-controller/policy.json` at the repository root. The format is JSON, like
every other Workflow/Controller config, and stdlib-parseable.

The Controller reads the policy from a **committed tree**, never the working tree:
- the trunk-start and bind preflights read `HEAD:.workflow-controller/policy.json` (at bind,
  `HEAD` is the trunk tip);
- the adopt row reads the policy at the adopted branch point, never at the milestone branch's
  `HEAD`;
- CI reads the policy of the commit it validates;
- a bound milestone uses the **policy snapshot stored in its binding record** (bytes and
  SHA-256).

This way a worker editing the file, or a milestone that changes the policy on its own branch,
cannot change the rules for the milestone in flight. A changed policy takes effect for the next
binding.

Reference file (the exact CP2 content):

```json
{
  "schema_version": 1,
  "trunk": {"branch": "main", "remote": "origin"},
  "forge": {"kind": "github", "repository": "RodrigoFAbreu/workflow-controller"},
  "milestone_branches": {
    "enabled": true,
    "branch_format": "milestone/{work_item_id}",
    "pull_request": {"draft": true, "ready_requires_green_checks": true}
  },
  "release": {
    "enabled": true,
    "trigger": "version_change",
    "version_source": {"kind": "pyproject", "path": "pyproject.toml"},
    "version_scheme": "semver",
    "tag_format": "v{version}",
    "abandoned_tags": ["v1.1.0"],
    "build": {"kind": "command",
              "argv": ["python", "-m", "pip", "wheel", "--no-deps", "-w", "dist", "."],
              "env": {"WORKFLOW_CONTROLLER_RELEASE_TAG": "{tag}"}},
    "verify": {"kind": "command",
               "argv": ["python3", "tools/release.py", "verify-wheel", "{artifact}",
                        "--tag", "{tag}", "--commit", "{commit}"]},
    "artifacts": {"paths": ["dist/workflow_controller-{version}-py3-none-any.whl"],
                  "checksums": "SHA256SUMS"},
    "publication": {"kind": "github_release", "title": "{tag}",
                    "notes": "workflow-controller {tag}"}
  }
}
```

Validation rules:
- Unknown keys at any level refuse.
- `schema_version` must equal 1.
- `branch_format` must contain `{work_item_id}` exactly once and render to a valid ref
  (`git check-ref-format --branch`) for every id matching Workflow's
  `^[a-z0-9][a-z0-9_-]{0,63}$`. It must not render to the trunk name.
- `tag_format` must contain `{version}` exactly once, render to a valid tag ref, and be
  invertible (the prefix and suffix around `{version}` identify the version), so the transaction
  can parse versions back out of tags.
- `abandoned_tags` (optional, default empty) lists tags that exist and must never get a release.
  Each entry must render from `tag_format`, which means its version parses. This is the explicit
  acknowledgement for a legacy orphan tag like `v1.1.0` (see "Release transaction").
- Placeholders are a closed set: `{work_item_id}`, `{version}`, `{tag}`, `{commit}`,
  `{artifact}`. Anything else refuses.
- `forge.repository` must be `OWNER/NAME`.

The adapter registries are closed dicts; an unknown `kind` refuses and names the known ones:
- version source: `pyproject`. It reads the static `[project].version` with `tomllib`, and
  refuses a `dynamic` version or a missing version. Extension point for `gradle-properties`,
  `package-json`, `command`.
- version scheme: `semver`. Plain `MAJOR.MINOR.PATCH`, the same rule as
  `buildinfo.SEMVER_RE`, with a total order.
- build/verify: `command` (argv plus env templates, run without a shell).
- publication: `github_release`.

Only what the reference adopter needs is implemented.

Activation is two independent switches: `milestone_branches.enabled` (Controller runtime) and
`release.enabled` (CI transaction). Absent file = both off (I1).

### Version authority (CP1)

- `pyproject.toml`: `[project] version = "1.1.1"`, static. Remove `dynamic` and
  `[tool.setuptools.dynamic]`.
- `controller/version.py` stops holding a value. It becomes a stdlib-only resolver that still
  imports nothing from `controller` (`test_package_structure`):
  - `source_version(code_root) -> str` parses `<code_root>/pyproject.toml` `[project].version`
    and validates it with `SEMVER_RE`;
  - `package_version(code_root) -> str` enumerates `importlib.metadata.distributions(path=
    [str(code_root)])` (that directory only, never all of `sys.path`). It requires exactly one
    `workflow-controller` distribution whose `RECORD` lists `controller/__init__.py`, and returns
    its `Version`.
  - Both raise `ValueError` naming the failure.
- `identity.resolve_runtime`:
  - `package`: `package_version(code_root)` must equal `BUILD_INFO.json`'s `version` (passed
    as `expected_version`). A disagreement is `unidentified` ("partially upgraded"), as a digest
    mismatch already is.
  - `source`: `source_version(code_root)`.
  - The `package_version` distribution name (`workflow-controller`) and the
    `controller/__init__.py` `RECORD` probe identify the Controller itself, not an adopter's
    package. They must not be "generalised" into policy.
- **The pin's version** (`materialise` writes `SOURCE_PIN.json`'s `version`, `identity.py:625,635`):
  - for a source snapshot, `source_version(<snapshot>)`, which reads the snapshot's own
    `pyproject.toml` (`_SNAPSHOT_FILES` already copies it). The value never comes from the
    parent's import-time value, so a `--allow-dirty-source` snapshot whose `pyproject.toml`
    differs reports the snapshot's version;
  - for a package snapshot (`_extract_package` copies only `controller/`, so there is no
    `*.dist-info` and no `pyproject.toml`): the `version` of the snapshot's own
    `controller/BUILD_INFO.json`. `resolve_runtime` has already validated that file against
    the distribution metadata in the parent.
  - `pin()` reads only `SOURCE_PIN.json`'s `version` for a pinned child. There is no fallback. A
    missing or non-semver value raises `SourceSnapshotError`. Every existing pin already carries
    the field.
  - `unidentified`: whichever of the two readers succeeds for `code_root`, else the literal
    `unknown`. Unidentified runtimes already cannot launch workers, so no record carries it.
  - The `ControllerIdentity.version` default becomes a required value resolved by the caller.
- `cli.version_text` prints the resolved runtime version. `--version` line 1 stays exactly
  `workflow-controller <version>`.
- `handoff`: the origin's approved version is read from `HEAD:pyproject.toml` with `tomllib`
  (informational, as today).
- `setup.py`'s build hook takes the version from `self.distribution.get_version()` (setuptools'
  static read of `pyproject.toml`) and no longer loads `version.py`.
- `tools/release.py` (TBR-R2-005): `main()` stops reading `version_module.__version__`.
  - **CP1:** every subcommand that needs the version (`version`, `verify-tag`, `verify-wheel`)
    reads `version.source_version(<repository root>)`, the checked-out `pyproject.toml`. CP1 also
    adapts `verify-tag` to require the static form. No policy exists yet at CP1.
  - **CP4:** `version` prints the policy's version-source value at the checked-out commit, and
    the new `classify`/`build`/`verify`/`publish` subcommands read the version the same way.
    `verify-wheel` keeps `source_version(<repository root>)`, because the build job checks out
    exactly the target commit. `verify-tag` is replaced by the classification and removed in CP5.

### Target-repository Git boundary (`controller/gitrepo.py`, CP3)

Every function takes the repository root and an injectable runner, returns typed results, and
raises `GitOperationError` (new, subclass of `ControllerError`) with argv, exit code and stderr
as evidence:

- reads: `head_state()` (attached branch or detached, commit), `ref_commit(ref)`,
  `is_ancestor(a, b)`, `ahead_behind(a, b)`, `tracked_changes()` (`status --porcelain
  --untracked-files=no`), `common_dir()`, `remote_url(remote)`, `ls_remote(remote, refs)` (with
  peel), `tag_commit(tag)`, `merge_base(a, b)`, `first_parent_log(a, b)`, `show(rev, path)`
  (with "absent at that revision" distinct from failure), `commit_trailers(commit)`,
  `worktree_branches()` (`git worktree list --porcelain`: the branch, if any, each worktree
  has checked out, keyed by worktree path; TBR-R4-002);
- `fetch(remote, refspecs)`: explicit refspecs into `refs/remotes/<remote>/...`, never `--force`
  onto a local branch and never `--prune`. Tags come only from an explicit
  `refs/tags/*:refs/tags/*` fetch in CI, and that fetch fails, rather than overwrites, when a
  local tag differs;
- `create_and_switch(branch)`: `git switch -c <branch>` (refuses if it exists; carries the
  working tree);
- `switch(branch)` (close-out only), `fast_forward(branch, onto)` (`merge --ff-only` while on
  it);
- `push_branch(remote, branch)`: after `fetch`, refuses unless the remote ref is absent or an
  ancestor of the local tip, then `git push <remote> refs/heads/B:refs/heads/B` with no `+` and
  no `--force*`;
- `create_annotated_tag(tag, commit, message)` and `push_tag(remote, tag)`
  (`refs/tags/T:refs/tags/T`, no `+`). A rejected push is re-read, never retried with force;
- `merge_trunk(remote, trunk)`: the integration primitive. It requires a clean tracked tree,
  runs `git merge --no-ff --no-edit <remote>/<trunk>`, and on conflict runs `git merge --abort`,
  then refuses with the conflicting paths. It is used by nothing in this milestone's lifecycle;
  wiring it is the follow-up integration milestone's decision.

A static test scans `controller/*.py` for subprocess argv literals and fails on `--force`,
`--force-with-lease`, a `+`-prefixed refspec, `reset --hard`, `rebase`, `commit --amend`,
`branch -D`/`-d`, `tag -f`/`-d`, `push --delete`, and `update-ref -d`. Every mutating Git
spelling must live in `gitrepo.py`.

### GitHub boundary (`controller/forge.py`, CP3)

`Forge` is a small protocol with one implementation, `GhForge(repository, runner)`. It passes
`--repo OWNER/NAME` on every call, so a checkout with several remotes can never resolve to an
upstream or fork.

- `repository_identity()`: `gh repo view R --json nameWithOwner,url`, which must equal the
  policy.
- `list_prs(head, base)`: `gh pr list --repo R --head <branch> --state all --json
  number,state,isDraft,headRefName,headRefOid,baseRefName,isCrossRepository,url,mergedAt,mergeCommit`.
- `view_pr(n)`: same fields.
- `create_draft_pr(head, base, title, body) -> number`: parses the returned URL, then re-reads
  with `view_pr`.
- `mark_ready(n)`: `gh pr ready`.
- `pr_checks(n)`: `gh pr checks --json name,state,bucket`. `gh` uses its exit code for normal
  outcomes, so the classification is by exit code and output together (TBR-R2-007):
  - exit 0, or exit 8 (checks pending), or exit 1 with stdout that parses as the expected JSON
    array (some check failed): the parsed checks, classified by `bucket`;
  - exit 1 with the "no checks reported" message on stderr and no JSON on stdout: `no_checks`,
    its own outcome, distinct from failure;
  - anything else, including exit 8 or 1 without parseable JSON: `ForgeUndecidableError` (I9).

  `bucket` is a closed set, the one `gh` 2.101.0 documents: `pass`, `fail`, `pending`,
  `skipping` and `cancel` (TBR-R3-003). A check with any other `bucket` value, or none, makes the
  whole read `ForgeUndecidableError`. It is never guessed into one of the five.
- releases:
  - `view_release(tag)` (`tagName,isDraft,assets,url`), where "not found" is a distinct
    outcome and anything else unexpected refuses (I9);
  - `create_release(tag, files, title, notes)` (`--verify-tag`, never `--draft` and never
    `--clobber`);
  - `upload_assets(tag, files)` (no `--clobber`, draft only);
  - `publish_draft(tag)` (`gh release edit T --draft=false`);
  - `download_assets(tag, dir)`.

There is **no** merge, close, delete, edit-body or comment operation. A static test scans the
subprocess argv literals in `controller/*.py`, like the Git scan. It fails on the element pairs
`"pr", "merge"`, `"pr", "close"` and `"release", "delete"`, on the element `"--clobber"`, and on
`"api"` as the element that immediately follows `"gh"`. Ordinary identifiers and prose are not
scanned.

`tests/fake_gh.py` is an executable fake with the same env-var-driven style as `fake_claude.py`.
It keeps PRs and releases in a JSON state file, reads tags from the disposable bare origin
through Git, logs every argv to an invocation file, and can inject auth, network, 500 and
malformed-output failures per subcommand. Its `pr checks` reproduces `gh`'s own exit codes: 8
with JSON for pending checks, 1 with JSON for a failing check, and 1 with the "no checks
reported" stderr message and no JSON. It can emit every documented `bucket`, including `cancel`,
and an out-of-set one for the undecidable test. It models two further GitHub behaviours
(TBR-R5-001, TBR-R5-002): `pr create` fails, as GitHub does, when the head has no commit beyond
the base in the bare origin (`git rev-list --count <base>..<head>` is 0), and `pr list --head`
filters by head branch **name** over every stored PR, including closed and merged ones from an
earlier branch of the same name. `pr create` also fails, as GitHub does ("A pull request already
exists for ..."), when a stored PR with the same head and base is open (TBR-R6-002). A fixture
that needs two open PRs for one head and base, which the real forge cannot produce, seeds the
state file directly (TBR-R6-003).

### Milestone branch binding (`controller/milestone_branch.py`, CP6)

**Durable record.** `<runtime_root>/repositories/<repo_key>/milestones/<work_item_id>.json`,
plus an append-only `.../<work_item_id>/events.jsonl`. The log spans every binding of one id: a
re-bind after `--abandon` renames the record aside but not the log, and every event carries the
record's `binding_generation`, so each line names the binding it belongs to (TBR-R5-002). `repo_key` is the SHA-256 of the
canonical `git rev-parse --git-common-dir`, shared by every worktree of one repository, which
leaves room for roadmap §6. All writes go through the `runtime.write_json`/`append_jsonl` APIs,
so write containment holds. The record is created with O_EXCL semantics, so two processes cannot
both create one. Records are keyed by the common directory, but the lifecycle lock is per
worktree. Two worktrees could therefore both reach one binding's mutating steps. This plan relies
on Git's own rule that a branch is checked out in at most one worktree, in two forms
(TBR-R4-002):
- every branch-side mutating step first requires `HEAD` attached to the bound branch;
- every record write made **without** `HEAD` on the bound branch ("Close-out from trunk" and the
  `milestone-binding` acknowledgement below) first requires, from a fresh read of
  `worktree_branches()`, that the bound branch is not checked out in any worktree other than
  this one. Otherwise it refuses, naming that worktree's path. So a trunk-side write and a
  branch-side step for the same binding can never run at the same time.

Broader multi-worktree concurrency stays out of scope (roadmap §6).

```json
{"schema_version": 1, "work_item_id": "...", "repository": {"common_dir": "...",
 "worktree_root": "...", "remote": "origin", "forge_repository": "OWNER/NAME"},
 "policy": {"sha256": "...", "snapshot": {"...": "..."}},
 "trunk": "main", "branch": "milestone/<id>", "branch_point": "<sha>",
 "workflow_base_commit": "<sha>", "binding_generation": 1, "state": "BRANCH_PLANNED",
 "pr": null, "superseded_prs": [], "merged_head": null, "last_observation": null,
 "updated_at": "..."}
```

`binding_generation` is 1 plus the number of `<work_item_id>.abandoned-<n>.json` records
present, counted immediately before the O_EXCL create, after any rename (TBR-R6-004). Counting
before the rename would give generation 1 to both the old and the new binding.

`merged_head` is `H` (below), recorded by every write of `MERGED`, whether reached through a PR
or through the PR-less close-out. Close-out step 3, from the branch and from trunk, reads `H`
from it, so it never depends on `pr` being set (TBR-R6-001). `last_observation` includes the
branch tip each preflight observed (`tip`), which is the "last observed tip" below.

States: the main chain is `BRANCH_PLANNED -> BRANCH_BOUND -> PR_PLANNED -> PR_OPEN -> READY ->
MERGED -> CLOSED`. The complete transition relation is the table below (TBR-R5-004). Every record
write is checked against it, and any other write raises `BranchBindingError` and writes nothing.
"Merged-PR handling" stands for its three possible outcomes, `MERGED`, `MERGED_REWRITTEN` and
`MERGED_BEFORE_ACCEPTANCE` ("Close-out", step 1 and step 2):

| From | To | Writer |
| --- | --- | --- |
| (no record) | `BRANCH_PLANNED` | bind step |
| (no record) | `BRANCH_BOUND` | adopt row |
| `BRANCH_PLANNED` | `BRANCH_BOUND` | bind step, or the `BRANCH_PLANNED` adopt/collision rows |
| `BRANCH_BOUND` | `PR_PLANNED` | PR creation step 2 |
| `BRANCH_BOUND` | `MERGED`, `MERGED_BEFORE_ACCEPTANCE` | PR-less close-out, from the branch or from trunk (TBR-R6-001) |
| `PR_PLANNED` | `BRANCH_BOUND` | PR creation step 4, 0 matches, the creation condition false and the PR-less close-out not applicable (TBR-R5-001) |
| `PR_PLANNED` | `MERGED`, `MERGED_BEFORE_ACCEPTANCE` | PR creation step 4, 0 matches, through the PR-less close-out, from the branch or from trunk (TBR-R6-001) |
| `PR_PLANNED` | `PR_OPEN` | PR creation step 5 |
| `PR_PLANNED` | `PR_CLOSED_UNMERGED` | PR creation step 4, closed-unmerged row |
| `PR_PLANNED` | merged-PR handling | PR creation step 4, merged row |
| `PR_OPEN` | `READY` | readiness |
| `PR_OPEN`, `READY` | `PR_CLOSED_UNMERGED` | per-preflight re-verification, or close-out from trunk step 1 |
| `PR_OPEN`, `READY` | merged-PR handling | per-preflight re-verification, or close-out from trunk step 2 |
| `PR_CLOSED_UNMERGED` | `PR_OPEN` | reopen exit (branch-side preflight only) |
| `PR_CLOSED_UNMERGED` | merged-PR handling | reopen exit, or close-out from trunk step 2 |
| `PR_CLOSED_UNMERGED`, `MERGED_BEFORE_ACCEPTANCE` | `BRANCH_BOUND` | `milestone-binding --new-pr` |
| `PR_CLOSED_UNMERGED`, `MERGED_BEFORE_ACCEPTANCE` | `ABANDONED` | `milestone-binding --abandon` |
| `BRANCH_PLANNED` | `ABANDONED` | `milestone-binding --abandon`, only with `milestone/<id>` absent locally and on the remote and the plan discarded (TBR-R7-001) |
| `BRANCH_BOUND` | `ABANDONED` | `milestone-binding --abandon`, only in the plan-stage window: the local tip equal to `branch_point`, no `<remote>/milestone/<id>`, and the plan discarded (TBR-R8-001) |
| `MERGED` | `CLOSED` | close-out step 3, or close-out from trunk step 3 |
| `ABANDONED` | (renamed to `<work_item_id>.abandoned-<n>.json`) | bind step of a re-planned id |

`CLOSED` and `MERGED_REWRITTEN` have no outgoing transition. A rewrite of the same state (for
example `last_observation` in `PR_OPEN`, the one-time gate flag of `MERGED_REWRITTEN`, or the
re-bind's new `T` in `BRANCH_PLANNED`) is not a transition and is always legal.

Every state belongs to exactly one of three classes (TBR-R4-001). Rule 1, rule 3.1 and every
other rule below use this classification and no other:
- **non-terminal:** `BRANCH_PLANNED`, `BRANCH_BOUND`, `PR_PLANNED`, `PR_OPEN`, `READY`, `MERGED`.
  The Controller drives these;
- **refusal (blocking until an exit):**
  - `MERGED_BEFORE_ACCEPTANCE`: merged before the work item's acceptance commit (see "Close-out");
  - `PR_CLOSED_UNMERGED`: the PR was closed without merge.

  Both block every step that reaches them, from the branch or from trunk, until one of the exits
  below moves the record out. The block is a property of the record alone. It is never
  conditioned on the work item's phase, which trunk cannot see;
- **terminal (never blocking):**
  - `CLOSED`: the normal end;
  - `MERGED_REWRITTEN`: merged, but the accepted head is not on trunk (a squash or rebase
    merge). Informational: the PR is merged and nothing remains to drive;
  - `ABANDONED`: written only by the acknowledgement below.

**Outcome matrix** (TBR-R6-001, the reviewer's convergence suggestion). Every classified state
has at least one row, and a split state's rows partition it (TBR-R7-004): `BRANCH_BOUND` is
split by the PR creation condition (CP7) and by whether the bound item is `MILESTONE_COMPLETE`
in the tip's committed `WORKFLOW_STATE.json`. "Branch" is a preflight with
`HEAD` attached to the bound branch (resolution rule 1), and "trunk" is one with `HEAD` attached
to trunk (rule 3.1). Every cell names a writer, a gate with a named exit, or a refusal with a
named exit. Nothing else is an admissible outcome, and a state added later needs its row first.
From the branch, rule 1's `bound_item_missing` gate (the bound item has no entry in the working
tree's `WORKFLOW_STATE.json`) precedes the branch cell of every non-terminal state except
`BRANCH_PLANNED`, whose completion rows run first. So no worker, PR or close-out step ever runs
with the bound item gone (TBR-R8-001):

| State | Branch | Trunk |
| --- | --- | --- |
| `BRANCH_PLANNED` | the `BRANCH_PLANNED` adopt/collision rows complete it to `BRANCH_BOUND`; otherwise the first matching refusal row of the adopt/collision table (the forge/common-dir row, the branch-not-descending row, or the catch-all), whose exit depends on the observation as in the trunk cell (TBR-R7-001; manual external round 1, `O5`) | the re-bind row (a re-run of the bind, which re-reads the policy snapshot at the new branch point), or switch-and-complete; otherwise the first matching refusal row (the forge/common-dir row, the branch-not-descending row, or the catch-all), naming the observation and its exit (TBR-R7-001): (a) a local or remote `milestone/<id>` at `T` or a descendant of it: switch to it, after which the complete-from-descendant row writes `BRANCH_BOUND`; (b) a `milestone/<id>` whose tip does not descend from `T`: remove or rename it, after which the re-bind row or (c) applies; (c) no `milestone/<id>` locally or on the remote, and a bind-step precondition failing at the current trunk tip: create `milestone/<id>` at the current trunk tip if it descends from `T`, else at `T`, and switch to it (the complete-from-descendant row then applies); or, if the plan was discarded, `milestone-binding --abandon`. The refusal names `--abandon` only when its precondition holds, and create-and-switch otherwise, never both (TBR-R8-001 usability note). Exit (b) is the refusal of the branch-not-descending row, which precedes the catch-all (TBR-R8-002) |
| `BRANCH_BOUND`, creation condition true | PR creation (writes `PR_PLANNED`) | blocks, naming the branch (exit: switch to it) |
| `BRANCH_BOUND`, condition false, not `MILESTONE_COMPLETE` | proceed: the worker runs, and its next commit makes the condition true | blocks, naming the branch (exit: switch to it; if the branch exists neither locally nor on the remote, the message names restoring it at the last observed tip). When `--abandon`'s `BRANCH_BOUND` precondition holds (the plan-stage window with the plan absent from trunk's working tree), the message names both exits, because trunk cannot tell a discarded plan from one stashed before the switch: `milestone-binding --abandon` if the plan was discarded, or switch back to the branch and restore the plan (`git stash pop`, or `git restore`) if it was stashed (TBR-R8-001; manual external round 1, `O3`) |
| `BRANCH_BOUND`, condition false, `MILESTONE_COMPLETE` | PR-less close-out (writes `MERGED`, then `CLOSED`; or `MERGED_BEFORE_ACCEPTANCE`) | PR-less close-out from trunk (same writes) |
| `PR_PLANNED` | PR discovery rows (writers, or refusals naming their exits) | the same discovery, read-only except for its stated writes, row by row (TBR-R7-002): excluded open, 2+ open and 2+ merged refuse with their own exits (close the excluded PR on GitHub; close the extras; the lost-runtime-root recovery); 1 merged is close-out from trunk step 2; 1 open refuses, naming the branch (exit: switch to it, where the adopt row runs); closed-unmerged writes `PR_CLOSED_UNMERGED` and refuses, naming its exits; 0 matches reach the PR-less close-out from trunk when it applies, and otherwise refuse, naming the branch (exit: switch to it), writing nothing |
| `PR_OPEN` | re-verification, readiness (writes `READY`), or a readiness gate naming its exit | close-out from trunk (an open PR refuses, naming the branch) |
| `READY` | the merge gate (exit: merge on GitHub) | close-out from trunk |
| `MERGED` | close-out step 3 (writes `CLOSED`), or its gate (exit below) | close-out from trunk step 3 (same) |
| `PR_CLOSED_UNMERGED` | the reopen re-read (writer), else gate `pr_closed_unmerged` (exits: reopen, `--new-pr`, `--abandon`) | close-out from trunk steps 1-2, else refusal naming the same exits |
| `MERGED_BEFORE_ACCEPTANCE` | gate `merged_before_acceptance` (exits: `--new-pr`, `--abandon`, as that gate states) | refusal naming the same exits |
| `CLOSED` | gate `switch_to_trunk` | never blocks |
| `MERGED_REWRITTEN` | its one-time gate, then `switch_to_trunk` | its one-time gate, then never blocks |
| `ABANDONED` | gate `switch_to_trunk` | never blocks (a re-plan of the id renames it) |

From trunk, every cell that needs the branch tip `H` (the three `BRANCH_BOUND` cells, which are
told apart at `H`, and the `PR_PLANNED` cell's 0-match row) first reads `H` as the PR-less
close-out does: the local `milestone/<id>` tip, else `<remote>/milestone/<id>`. If neither
exists, the preflight refuses, naming the last observed tip, and writes nothing; the exit is to
restore the branch there by hand (`git branch milestone/<id> <sha>`) (TBR-R7-004).

**Refusal-state exits.** Each refusal state has an in-scope, tested exit. None of them touches a
ref or a PR (I2, I3):
- **Reopen** (`PR_CLOSED_UNMERGED` only, automatic). A human reopens the PR on GitHub. The next
  branch-side preflight (rule 1) re-reads the recorded PR number with `view_pr`. If it is open
  again, and its number, head ref, base ref, repository and non-cross-repository flag still match
  (I6), the record returns to `PR_OPEN` and the step continues as for any `PR_OPEN` record. If it
  was reopened and then merged, the merged-PR handling applies ("Close-out"). From trunk, a
  reopened PR blocks like any open PR, naming the branch. The branch-side preflight is the only
  writer of this transition.
- **`--new-pr`** (both refusal states). The operator runs
  `workflow-controller --work-item <id> milestone-binding --new-pr <repo>`. After the common
  checks below, it moves the recorded PR number, if any, into the record's `superseded_prs`
  list, sets `pr` to `null`, and writes `BRANCH_BOUND`. The milestone then continues on the same branch. The
  next branch-side preflight creates a new Draft PR as soon as the PR creation condition holds
  (CP7), and PR discovery never matches a superseded number. This is the only exit from
  `MERGED_BEFORE_ACCEPTANCE` that continues the work item. After such a merge, trunk already
  carries the branch up to `H`, which is normally the branch tip. GitHub refuses a PR whose head
  has no commit beyond its base, so the creation condition is false until the next Workflow
  commit on the branch: until then the preflight skips PR creation, the record stays
  `BRANCH_BOUND`, and the worker step runs (TBR-R5-001). The new PR's readiness then meets
  `integration_required` (trunk holds the merge commit, which the branch does not), and the
  supported manual procedure applies.
- **`--abandon`** (both refusal states, with one extra precondition). The operator runs
  `workflow-controller --work-item <id> milestone-binding --abandon <repo>`, which writes
  `ABANDONED`. Extra precondition: after `fetch`,
  `<remote>/<trunk>:docs/ai-workflow/WORKFLOW_STATE.json` has no non-terminal entry for the
  work item. Workflow 2.5.1 has no abandonment transition: `TERMINAL_PHASES` is
  `{"MILESTONE_COMPLETE"}` (`scripts/workflow_state.py:321`). So the Controller cannot retire a
  work item whose non-terminal state has already reached trunk. A trunk start above it would
  either drive it on trunk or refuse forever. `MERGED_BEFORE_ACCEPTANCE` normally fails this
  precondition, because the merge carried the item's non-terminal state to trunk. The refusal
  then names `--new-pr`. A `PR_CLOSED_UNMERGED` record normally passes it: nothing of the item
  reached trunk, because the bind carries the plan-stage files to the branch.

  `--abandon` is also admitted for a `BRANCH_PLANNED` record whose `milestone/<id>` exists
  neither locally nor, after `fetch`, on the remote (TBR-R7-001, catch-all sub-case (c)). The
  bind created nothing, so there is no ref or PR to leave behind (I2, I3). This is the exit for
  an operator who discarded the plan after the crash. Its precondition is the one above, plus the
  same rule applied locally: `HEAD` is on trunk, and neither the working tree's
  `WORKFLOW_STATE.json` nor `HEAD:docs/ai-workflow/WORKFLOW_STATE.json` has a non-terminal entry
  for the item. The committed read catches a plan approval commit still on local trunk under a
  working tree edited by hand (TBR-R8-005): a later `git checkout .` would otherwise restore the
  item as a bind candidate beside an `ABANDONED` record, and the bind would refuse "approved on
  trunk". Otherwise the next trunk start would find the item again as a bind candidate, and a
  plan approved on trunk would refuse there with no record left to complete. When the plan is
  still present, the catch-all names the other exit of (c): create the branch and switch to it.

  `--abandon` is also admitted for a `BRANCH_BOUND` record in the plan-stage window (TBR-R8-001):
  the bind completed, and then the operator discarded the uncommitted plan, which under 1.1.1 is
  a complete undo. Its precondition is the remote-trunk one above, plus all of these:
  - the local `milestone/<id>` tip equals the record's `branch_point`, and after `fetch` no
    `<remote>/milestone/<id>` exists. So nothing was committed on the branch and nothing was
    pushed (the push happens only at PR creation), and `pr` is `null`;
  - the record's own history agrees (manual external round 1, `O2`): `superseded_prs == []`, so
    no PR ever existed for this binding, and `last_observation` is `null` or its `tip` equals
    `branch_point`, so no preflight ever observed a commit on the branch. This makes "nothing
    was ever pushed and no PR existed" an invariant of the record, not an assumption about the
    operator's history;
  - `HEAD` is on trunk or on the bound branch (common check 2), and neither the working tree's
    `WORKFLOW_STATE.json` nor `HEAD:docs/ai-workflow/WORKFLOW_STATE.json` has a non-terminal
    entry for the item. With the tip at `branch_point`, `HEAD`'s committed state is trunk's
    state at `T` from either side.

  The leftover local `milestone/<id>` at `T` is covered by the "An `ABANDONED` record" paragraph
  below: with `HEAD` on it, rule 1 gates `switch_to_trunk`, and from trunk the record never
  blocks, so the next trunk start selects bare `/milestone-plan` on trunk. With the tip past
  `branch_point`, the bound item is committed on the branch, so the plan was not discarded, only
  edited in the working tree. The exit is then to restore it (`git restore
  docs/ai-workflow/WORKFLOW_STATE.json` and the plan files), and `--abandon` refuses, naming it.

**Common checks of `milestone-binding`**, in order, under the lifecycle lock. Any failure refuses
(exit 20) and writes nothing:
1. a binding record exists for `--work-item` in this repository's `repo_key`, and it is in a
   refusal state, or it is `BRANCH_PLANNED` or `BRANCH_BOUND` and the disposition is `--abandon`
   (sub-case (c) and the plan-stage window above; `--new-pr` from either refuses). Any other
   state refuses, naming it;
2. `HEAD` is attached to trunk or to the bound branch, and the bound branch is not checked out in
   any other worktree (the concurrency rule under "Durable record");
3. a fresh `view_pr` of the recorded number still shows the state that produced the record:
   closed and unmerged for `PR_CLOSED_UNMERGED`, merged for `MERGED_BEFORE_ACCEPTANCE`. A
   reopened PR refuses and names the reopen exit (switch to the branch). Undecidable reads
   refuse (I9). A `MERGED_BEFORE_ACCEPTANCE` record written by the PR-less close-out has
   `pr == null`, so there is no PR to re-read and this check is skipped (TBR-R6-001). A
   `BRANCH_PLANNED` or `BRANCH_BOUND` record also has `pr == null` and skips it;
4. the disposition's own precondition (`--abandon` only, above);
5. `--new-pr` only (TBR-R7-003): if the record is `MERGED_BEFORE_ACCEPTANCE` and, after `fetch`,
   `<remote>/<trunk>`'s committed `WORKFLOW_STATE.json` records the item `MILESTONE_COMPLETE`
   **without an acceptance commit**, refuse, naming `--abandon`. "Without an acceptance commit"
   means no acceptance commit `A` (CP7's definition, trailer required) on the first-parent path
   `branch_point..H`, where `H` is the branch tip read as the PR-less close-out reads it (the
   local `milestone/<id>` tip, else `<remote>/milestone/<id>`; if neither exists, refuse, naming
   the last observed tip). That is the path the PR-less close-out searches after `--new-pr`, so
   the check refuses exactly when `--new-pr` would lead back to `MERGED_BEFORE_ACCEPTANCE`. A
   human who accepted on the branch outside the Controller and merged again has `A` on that path,
   and `--new-pr` is admitted and converges to `CLOSED` (TBR-R8-003). This is evaluated exactly as
   the `merged_before_acceptance` gate evaluates its `--abandon`-alone case, so the command
   accepts only a disposition the gate names. Without this check `--new-pr` would write `BRANCH_BOUND`, and the next preflight's
   PR-less close-out would write `MERGED_BEFORE_ACCEPTANCE` again.

Then the command appends an `acknowledged` event (disposition, previous state, PR number,
operator-visible reason) to `events.jsonl`, writes the new record state, and exits 0. It
launches no worker and creates no job record. `--new-pr` and `--abandon` are mutually exclusive,
and exactly one is required. `--work-item` is required for this subcommand.

**An `ABANDONED` record** keeps governing its branch. With `HEAD` on the old branch, rule 1 finds
a terminal binding and gates with `switch_to_trunk`, so the leftover `milestone/<id>` is never
adopted, from trunk or from the branch: adopt (rule 2) applies only to a branch with no binding
record. From trunk, an `ABANDONED` record never blocks. If the same id is planned again on trunk
(Workflow 2.5.1 derives the id, fact 2), rule 3.2 admits it as a bind candidate. The bind step's
own precondition that no local or remote `milestone/<id>` exists then applies. The Controller
never deletes a branch (I2), so the operator removes the old branches first, and the runbook
says so. When the candidate has an `ABANDONED` record and that precondition fails, the refusal
names the leftover branch and the exact exit: `git branch -d milestone/<id>` for a local one
(`-D` only if the operator chooses to discard commits on it), and
`git push <remote> --delete milestone/<id>` for a remote one (manual external round 1, `O4`). The bind then renames the old record to `<work_item_id>.abandoned-<n>.json` (`n` the
next free integer) before the O_EXCL create of the new one. Work-item resolution never reads a renamed
record. Only PR discovery reads renamed records, and only for their PR numbers: the abandoned
binding's closed PR still has head ref `milestone/<id>`, and `gh pr list --head` filters by
branch name, so discovery for the new binding would otherwise find it (TBR-R5-002, CP7). A crash
between the rename and the create leaves no record, which is the ordinary unbound bind
candidate. The renamed record already exists at that point, so the exclusion survives the crash
window without anything being copied into the new record. The events log is not renamed (see
"Durable record").

**Remediation children never get their own binding** (TBR-R2-001). A work item with a non-null
`parent_work_item_id` (Workflow 2.5.1's `create_remediation_child_work_item`,
`scripts/workflow_state.py:10216`) is created in `PLANNING` with its `base_commit` at the
parent's implementation head, on the parent's branch, and without touching
`active_work_item_id`. It runs on its parent's bound branch under its parent's binding: that
binding's snapshot, preflight, sync and post-step verification govern every step of the child
exactly as they govern the parent's own steps. The child gets no branch, no PR and no binding
record, is never a bind candidate, and is never an adopt candidate. It needs none: its commits
land on the parent's branch and reach trunk through the parent's PR. The parent cannot reach
`MILESTONE_COMPLETE` while the child is incomplete (`incomplete_children`), so readiness is never
evaluated with a child in flight.

**Work-item resolution** (one order, used by every preflight; the first matching rule wins):
1. `HEAD` is attached to the branch of a binding record for this repository: that binding
   governs the step. The step's own target work item is still whatever `decide`'s existing
   selection picks (the bound work item, or one of its remediation children). The preflight
   only overrides it after acceptance (below), and only with the bound work item itself. Four
   cases gate or refuse instead (TBR-R3-004, TBR-R4-001, TBR-R8-001). They are not
   first-match-then-stop: a case whose writer leaves the record in a non-terminal state (the
   reopen re-read's `PR_CLOSED_UNMERGED -> PR_OPEN`, a reopened-then-merged PR's `MERGED`, or a
   `BRANCH_PLANNED` completion's `BRANCH_BOUND`) is followed, in the same preflight, by the
   `bound_item_missing` case before the resulting state's branch cell runs (manual external
   round 1, `O1`):
   - the binding is in a terminal state (`CLOSED`, `ABANDONED`, or `MERGED_REWRITTEN` after its
     one-time gate): the step gates with `switch_to_trunk`, naming the trunk branch. The
     Controller does not switch (I4), and the post-acceptance override never re-enters PR
     discovery or readiness from a terminal binding;
   - the binding is in a refusal state. `PR_CLOSED_UNMERGED` first re-reads the PR (the reopen
     exit): open again with matching identity returns the record to `PR_OPEN` and continues;
     merged goes to the merged-PR handling; still closed gates `pr_closed_unmerged`, naming the
     three exits (reopen on GitHub, `--new-pr`, `--abandon`). The gate also says that GitHub
     cannot reopen a PR whose head branch was deleted on GitHub: restore the branch there first,
     or use `--new-pr` or `--abandon` (round-5 usability note). It says the three exits are
     exclusive: after `--new-pr`, do not also reopen the old PR (TBR-R6-002). And it says that
     if the branch has already been merged into trunk by hand, `--new-pr` is the exit to take:
     the next preflight then finds nothing to create and runs the PR-less close-out (round-6
     usability note). `MERGED_BEFORE_ACCEPTANCE` gates `merged_before_acceptance`, naming
     `--new-pr` (and `--abandon` only if its precondition holds). When `<remote>/<trunk>`'s
     committed state already records the item `MILESTONE_COMPLETE` without an acceptance commit
     (a phase set by hand and merged; the acceptance commit is looked for on the first-parent
     path `branch_point..H`, exactly as common check 5 states, TBR-R8-003), `--new-pr` would only lead back to this state through the
     PR-less close-out, so the gate names `--abandon` alone, whose precondition then holds. The
     gate says why: without an acceptance commit, the PR-less close-out would record the same
     state again (round-7 usability note), and `milestone-binding --new-pr` refuses in this case
     (common check 5). No other step runs;
   - the binding is non-terminal and the bound work item has no entry in the working tree's
     `WORKFLOW_STATE.json`. A `BRANCH_PLANNED` record is first completed by its adopt/collision
     rows, which do not read the item, and the check then applies to the resulting
     `BRANCH_BOUND` record in the same preflight (for example the operator discarded the uncommitted plan after the
     bind): gate `bound_item_missing`, naming the bound item and branch, and launch no worker.
     This case is evaluated before `decide`'s selection is used. `decide` would otherwise take
     its no-work-item path, and `decision.decide_no_work_item` (`controller/decision.py:972`)
     always selects bare `/milestone-plan`, which would plan a second milestone on the bound
     branch. The gate names the exits: restore the plan files (`git restore`, or `git stash pop`
     if they were stashed), after which the step proceeds; or, when `--abandon`'s `BRANCH_BOUND`
     precondition holds ("Refusal-state exits"), `milestone-binding --abandon`, after which rule
     1 gates `switch_to_trunk`. `--abandon` is named only when its precondition holds;
   - `decide`'s selection names a work item that is neither the bound work item nor one of its
     remediation children (for example an explicit `--work-item` naming an unrelated top-level
     item): refuse, naming both ids;
2. `HEAD` is attached to a non-trunk branch whose name inverts `branch_format` to a work-item id
   with no binding record: that id is the adopt candidate (adopt/collision table);
3. `HEAD` is attached to trunk. Trunk's checked-out `WORKFLOW_STATE.json` cannot see a bound
   work item: the bind carries its state entry to the branch uncommitted, and the first commit
   that records it (the plan approval commit) is on the branch (TBR-R3-001;
   `target_state.read` reads the working tree). The one exception is a binding completed through
   the `BRANCH_PLANNED` catch-all's exit (c) after the approval commit already reached trunk.
   Trunk's state then shows the item, which is harmless, because this rule reads the records
   first (TBR-R8-004). So this rule reads the **binding records for
   this `repo_key`** first, and trunk's work items only after:
   1. every record in a non-terminal or refusal state (the classification under "Durable
      record") blocks, unless one of the stated reconciliations applies: the `BRANCH_PLANNED`
      rows of the adopt/collision table (the plan-stage window), "Close-out from trunk" (CP7)
      for a record in `PR_OPEN`, `READY`, `MERGED` or `PR_CLOSED_UNMERGED`, or its PR-less form
      for a record in `BRANCH_BOUND` or `PR_PLANNED` (TBR-R6-001; the outcome matrix above
      states each cell). A blocking record
      refuses, naming its work item and branch (I4: the Controller never switches to it). A
      refusal-state record's message also names its exits (`milestone-binding --new-pr` or
      `--abandon`, or reopening the PR). Terminal records (`CLOSED`, `ABANDONED`,
      `MERGED_REWRITTEN`) never block;
   2. otherwise the bind candidate is the non-terminal work item in trunk's state with
      `parent_work_item_id == null` and no binding record, or only an `ABANDONED` one (a re-plan
      of an abandoned id, above). Two or more such items refuse, naming them. None means the
      trunk-start preflight;
4. anything else (detached `HEAD`, a non-trunk branch that does not invert `branch_format`)
   refuses.

A child whose parent has no binding record (a policy committed mid-milestone) is not a
candidate under any rule. Its parent is, and the parent's plan-stage precondition decides.

**Trunk-start preflight** (resolution rule 3 found no blocking binding record and no
non-terminal work item, policy active, before bare `/milestone-plan`):
- `HEAD` is attached to the trunk branch;
- the tracked tree is clean;
- after `fetch`, local trunk equals `<remote>/<trunk>`.

A behind trunk gates "fast-forward trunk" (the Controller does not fast-forward at start: the
human may have local work). A diverged trunk refuses.

**Bind step** (resolution rule 3 names a bind candidate; a remediation child is never one).
Preconditions, all required:
- `HEAD` is attached to the trunk branch at local trunk tip `T`;
- the work item's phase is one of `PLANNING`, `SELF_REVIEWING_PLAN`,
  `AWAITING_EXTERNAL_PLAN_REVIEW` (v1), `AWAITING_LOCAL_PLAN_REVIEW`,
  `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `REVISING_PLAN` or `AWAITING_PLAN_APPROVAL`. This is
  an explicit set, because `KNOWN_PHASES` is a frozenset with no order. `AMENDING_PLAN` is
  excluded, since it always has `plan_approval` set;
- `plan_approval == null`, so nothing has been committed for it yet (fact 1);
- its `base_commit` is an ancestor of or equal to `T`;
- no local or remote `milestone/<id>` exists.

Actions: write `BRANCH_PLANNED` (with `T`), `git switch -c milestone/<id>`, verify that `HEAD`
is attached to it at `T`, then write `BRANCH_BOUND`. For a candidate with an `ABANDONED` record,
the write of `BRANCH_PLANNED` is preceded by renaming that record, as stated under "Durable
record".

If the plan approval commit already sits on trunk (a human approved before any Controller bind),
the Controller refuses with manual-recovery guidance. It never moves that commit.

**Adopt/collision table** (record absent unless stated; the first matching row wins, and the
last row makes the table total). The only record state that reaches a record-present row is
`BRANCH_PLANNED`: rule 1 resolves every other state with `HEAD` on its branch, and rule 3.1
reconciles it from trunk. So every record-present row, including the refusals, names the
`BRANCH_PLANNED` exit for its observation (TBR-R8-002). `<id>` is the candidate from resolution rules 2 and 3, which is
never a remediation child. A child's own id never names a branch, so a child on its parent's
bound branch is resolved by rule 1 and never reaches this table:

| Observed | Outcome |
| --- | --- |
| `HEAD` attached to `milestone/<id>`, and every adopt precondition below holds | adopt (a human created the branch, or the runtime root was lost): write `BRANCH_BOUND` with branch point `P` and the policy snapshot read at `P` |
| local `milestone/<id>` exists, `HEAD` on trunk | refuse (collision; never switch with a dirty tree) |
| only `<remote>/milestone/<id>` exists | refuse (another clone may own it) |
| `HEAD` attached to some other non-trunk branch | refuse |
| detached `HEAD` | refuse |
| record `BRANCH_PLANNED` whose `repository.forge_repository` or `repository.common_dir` differs from the repository | refuse, naming the recorded and the observed value; exit: restore the remote's URL to the recorded forge repository, or `milestone-binding --abandon` when its `BRANCH_PLANNED` precondition holds (TBR-R8-002). The policy is not compared: like every binding, a `BRANCH_PLANNED` record is governed by its recorded snapshot, and the re-bind row, being a re-run of the bind, re-reads the snapshot at the new tip |
| record `BRANCH_PLANNED`, branch absent, `HEAD` on trunk at the recorded `T`, or at a later local trunk tip `T'` at which every bind-step precondition holds | re-run the bind at the current tip (the crash came before `switch -c`, so nothing was created; a moved trunk rewrites `T` to `T'` and re-reads the policy snapshot at `T'`, a same-state write; `O5`) |
| record `BRANCH_PLANNED`, branch at `T` or at a descendant of it, `HEAD` on it | complete to `BRANCH_BOUND` (commits made after a crash between `switch -c` and the write are kept) |
| record `BRANCH_PLANNED`, branch at `T`, `HEAD` on trunk at `T` | switch to it (same commit, working tree preserved), then `BRANCH_BOUND` |
| record `BRANCH_PLANNED`, a local or remote `milestone/<id>` whose tip does not descend from the recorded `branch_point` `T` (with `HEAD` on it or on trunk) | refuse with exit (b) of the catch-all below: the bind did not create this branch, so remove or rename it, after which the re-bind row or (c) applies (TBR-R8-002) |
| any other observation (for example: record `BRANCH_PLANNED` with the branch absent and a bind-step precondition failing at the current trunk tip; `HEAD` on `milestone/<id>` with a failed adopt precondition) | refuse, naming the observation and the row it came closest to. For a `BRANCH_PLANNED` record the refusal also names the exit for its observation (TBR-R7-001): (a) `milestone/<id>`, local or remote, at `T` or a descendant of it (for example a crash between `switch -c` and the write, then commits on the branch, then `git switch <trunk>`): switch to it, and the complete-from-descendant row writes `BRANCH_BOUND`; (b) a `milestone/<id>` whose tip does not descend from `T`, which the bind did not create: remove or rename it (this observation matches the branch-not-descending row above, which states this exit; it is listed here so the split is complete, TBR-R8-002); (c) no `milestone/<id>` locally or on the remote, with a bind-step precondition failing (for example the plan approved on trunk after the crash, or trunk rewound below `base_commit`): create `milestone/<id>` at the current trunk tip if it descends from `T`, else at `T`, and switch to it, so the complete-from-descendant row applies; or, if the plan was discarded, `milestone-binding --abandon` ("Refusal-state exits") |

**Adopt preconditions** (all required; the bind step's "`HEAD` on trunk" and plan-stage phase
preconditions deliberately do not apply, because adopt must also recover a lost runtime root
mid-implementation or after acceptance):
- after `fetch`, `P = merge-base(<remote>/<trunk>, HEAD)` is defined;
- the work item has `parent_work_item_id == null`, and its `base_commit` is an ancestor of, or
  equal to, `P`;
- the work item is non-terminal, **or** it is `MILESTONE_COMPLETE` and its acceptance commit `A`
  (defined under CP7) is on the first-parent path `P..HEAD` (TBR-R2-008: a runtime root lost
  between `/accept-milestone` and the merge). The adopted binding then proceeds to PR discovery,
  which adopts the open PR, and to readiness;
- if `plan_approval` is set, its approval commit (the `Workflow-Plan-Approval` trailer for this
  id) is in `P..HEAD` on the first-parent path. If it sits on trunk, this is the "approved on
  trunk" manual-recovery refusal above;
- `<remote>/milestone/<id>` is absent, or is an ancestor of, or equal to, `HEAD`;
- `P:.workflow-controller/policy.json` exists, is admissible, and has
  `milestone_branches.enabled`. A policy edited on the milestone branch itself is never the one
  adopted.

**Every later preflight** for a bound, non-terminal item:
- `HEAD` is attached to the bound branch;
- the tip descends from the last observed tip (no rewrite);
- after `fetch`, the remote branch is absent or an ancestor of the tip. Anything else refuses.
  The refusal for a remote branch that is not an ancestor names the likely cause: GitHub's
  "Update branch" button, which pushes a merge commit to the milestone branch on the server
  (usability note, round 4).

In `MERGED` the last two checks do not apply. Close-out step 3's own comparison of the tip with
`merged_head` replaces them, so the exit from step 3's gate (moving unmerged commits off the
branch) is not refused as a rewrite (TBR-R6-001, outcome matrix).

**Sync:** when the local tip is ahead of the remote branch, `push_branch` fast-forwards it. The
push is persisted as an intent and the outcome is re-read with `ls_remote`.

### Draft PR lifecycle, drift, readiness, merge gate, close-out (CP7)

- **PR creation condition** (TBR-R5-001): the local tip is **not** an ancestor of, or equal to,
  `<remote>/<trunk>` as fetched by this preflight. That is "at least one commit ahead of the
  base", which is exactly what GitHub checks: `gh pr create` fails ("No commits between ...")
  for a head with nothing beyond its base. For a first PR this normally equals the old "a
  commit beyond `branch_point`" condition, because the bind step and adopt refuse a plan
  approval commit on trunk, so the plan approval commit on the branch is the earliest point.
  The one first-PR exception is a binding completed through the `BRANCH_PLANNED` catch-all's
  exit (c) after the approval commit already reached `<remote>/<trunk>` (TBR-R8-004). The branch
  then starts at a trunk tip that holds the approval commit, the condition stays false until the
  next branch commit, and the worker proceeds as in the plan-stage window. It also differs after a
  merge before acceptance: trunk then contains the branch tip, and the condition stays false
  until the next branch commit. While it is false the preflight skips PR creation, the record
  stays in `BRANCH_BOUND`, and the step proceeds, as in the plan-stage window. The one
  exception is a bound item that is already `MILESTONE_COMPLETE` in the tip's committed state:
  no worker step follows acceptance, so no further commit will make the condition true, and
  the PR-less close-out below applies instead (TBR-R6-001).
- **PR creation** (condition true, record `BRANCH_BOUND`, or a restart in `PR_PLANNED`). Steps:
  1. push the branch;
  2. write `PR_PLANNED`;
  3. `list_prs`, filtered to matches on head ref, base ref and `isCrossRepository == false`,
     and excluding the **excluded set** (TBR-R5-002): every number in the live record's
     `superseded_prs` (the `--new-pr` exit, TBR-R4-001), plus the `pr.number` and every
     `superseded_prs` number of each `<work_item_id>.abandoned-<n>.json` record for this
     `repo_key`. The renamed records are read-only audit files, and this is their only reader.
     The same listing also yields the **excluded open** PRs: members of the excluded set that
     match on head ref, base ref and repository and are open (TBR-R6-002);
  4. act on the matches, in this order (the first matching row wins, and the last makes it
     total):
     - 1 or more excluded open: refuse, naming each, saying it was superseded (`--new-pr`) or
       abandoned and must be closed on GitHub. It is never adopted, which would reintroduce
       TBR-R5-002's identity error, and no create is attempted: GitHub refuses a second open PR
       for the same head and base. Closing it on GitHub is the exit, and the next preflight
       re-runs this step;
     - 2+ open: refuse, naming them. This row is defensive: GitHub keeps at most one open PR
       per head and base, so the CP7 fixture seeds this state directly (TBR-R6-003). Closing the
       extras on GitHub is the exit;
     - 2+ merged: refuse, naming them. Within one binding every merged PR ends in `MERGED`,
       `CLOSED`, `MERGED_REWRITTEN` or `superseded_prs`, so two non-excluded merged PRs mean the
       records that excluded the older ones were lost with the runtime root. The Controller
       cannot tell which PR is this binding's and never guesses. A merged PR cannot be closed,
       so the refusal names the lost-runtime-root recovery under "Migration / data-integrity
       notes" as the exit (TBR-R6-003);
     - 1 merged: the merged-PR handling under "Close-out";
     - 1 open: adopt it, and add any closed-unmerged matches to `superseded_prs`. If a human
       already marked it ready, the Controller never flips it back;
     - 0 open, 1 or more closed-unmerged: the highest number is this binding's PR, closed by a
       human between `gh pr create` and the record write. Write `PR_CLOSED_UNMERGED` with it as
       `pr` and any others in `superseded_prs`, and gate `pr_closed_unmerged`. Its exits then
       apply (reopen, `--new-pr`, `--abandon`), so a restart in `PR_PLANNED` is never stuck;
     - 0 matches: re-check the creation condition against the same fetch. If it is false (a
       human fast-forwarded trunk to the branch after step 2), run the PR-less close-out when
       the bound item is `MILESTONE_COMPLETE` in the tip's committed state (TBR-R6-001);
       otherwise write `BRANCH_BOUND` and proceed. If it is true, `create_draft_pr` with title
       `<work_item_id>` and a generated body naming the plan path and the marker line
       `<!-- workflow-controller: work_item=<id> -->`;
  5. re-read with `view_pr`, then write `PR_OPEN` with the number and URL.

  A restart in `PR_PLANNED` reruns step 3, so a crash between `gh pr create` and the record
  write adopts the created PR and never creates a second one.
- **Every preflight with a PR** (states `PR_OPEN` and `READY`) re-verifies the PR against I6:
  - a number that now names a different head, base or repository refuses;
  - `isDraft == false` set by a human is accepted as is. The Controller never flips it back;
  - a PR closed without merge becomes `PR_CLOSED_UNMERGED`, a gate for the human. It is never
    recreated automatically. Its exits (reopen, `--new-pr`, `--abandon`) are under "Durable
    record";
  - a merged PR goes to the merged-PR handling under "Close-out", whatever the record state.
- **Drift:**
  - after each fetch, record `fresh = is_ancestor(<remote>/<trunk>, HEAD)` and
    `behind = ahead_behind(...)` in `last_observation`;
  - before `MILESTONE_COMPLETE`, drift is informational only: it is shown by
    inspect/explain/status and does not block;
  - under Workflow 2.5.1 the Controller never integrates (boundary table).
- **Acceptance commit.** `A` is the first commit on the first-parent path `branch_point..tip`
  whose committed `WORKFLOW_STATE.json` records the work item as `MILESTONE_COMPLETE`, and whose
  first parent's committed state does not. `A` must also carry the `Workflow-Work-Item: <id>`
  trailer that `/accept-milestone` writes (`82fa6a8` is the shape). A missing trailer refuses:
  the phase changed outside `/accept-milestone`. `MILESTONE_COMPLETE` stays true for every later
  commit, so the phase alone cannot identify the accepted head. This is all local Git reads
  (`first_parent_log`, `show`, `commit_trailers`). A remediation child's own acceptance commit
  on the same branch (trailer `Workflow-Work-Item: <child-id>`) is never mistaken for the
  parent's `A`: it changes the child's phase, not the parent's, and its trailer names the child,
  so both halves of the definition exclude it (TBR-R2-001).
- **Readiness** (work item `MILESTONE_COMPLETE` in the branch's own committed
  `WORKFLOW_STATE.json`, record `PR_OPEN`), all required:
  1. `HEAD` is attached to the bound branch;
  2. the tracked tree is clean;
  3. the local tip is exactly the acceptance commit `A`. No commit follows it: not a human's
     direct commit, and not a release-preparation commit like `5832b16`/`00c3b71`. This is the
     same rule as I7;
  4. the local tip has been pushed, so the remote branch equals the local tip;
  5. `view_pr` shows open, head ref and base ref matching, `headRefOid` equal to `A`, and not
     cross-repository;
  6. fresh: `<remote>/<trunk>` is an ancestor of `A` (I7);
  7. if `ready_requires_green_checks`: at least one check is reported for that head, and every
     reported check's `bucket` is `pass` or `skipping`. The other three buckets (`pending`,
     `fail`, `cancel`) each fail this condition with their own gate below.

  Then `mark_ready` (skipped when already ready), re-read, and write `READY` with `A` recorded
  as `accepted_head`. Failed conditions produce a `HumanGate`, not an error:
  - condition 3 is `post_acceptance_commits`. It names the commits after `A`. The Controller
    never removes them (I2). A hand reset of the branch would trip the no-rewrite check of every
    later preflight, and the commits may already have been pushed. The resolution is therefore
    a human decision: merge anyway on GitHub (mark ready, "Create a merge commit"), and the
    merged-PR handling below converges from it. Otherwise the gate persists;
  - condition 6 is `integration_required`. It names the drift count and says that Workflow 2.5.1
    has no transition to re-establish review against an integrated base. The gate text gives the
    manual procedure: on GitHub, mark the PR ready and merge it with "Create a merge commit". This
    is the whole contract under Workflow 2.5.1; an automated integration path is left to the
    follow-up integration milestone;
  - condition 7 with no check reported yet, or any check pending, is `checks_pending`. Just after
    the final push GitHub commonly reports no checks, so this is the common case, not a failure;
  - a failing condition 7 is `checks_failing`;
  - condition 7 with no `pending` or `fail` check but at least one `cancel` check is
    `checks_cancelled` (TBR-R3-003). It names the cancelled checks, and the remedy is to re-run
    them on GitHub. Precedence when several buckets are present: `checks_failing`, then
    `checks_pending`, then `checks_cancelled`.
- **Merge gate:** at `READY` every run ends with `HumanGate("merge pull request #N", url)`. The
  gate text says to use "Create a merge commit", since squash and rebase merges take the reviewed
  commits off trunk (see below). The Controller never merges (I3).
- **Merged-PR handling and close-out.** Entered from `PR_OPEN` or `READY`, from
  `PR_CLOSED_UNMERGED` through the reopen exit, or from `PR_PLANNED` through PR discovery's
  merged row, whenever `view_pr` (or `list_prs` during creation) shows the PR merged (the
  transition table under "Durable record"). The PR-less close-out below reuses step 1 with no
  PR. `H` is the PR's final `headRefOid`. If `H` is not present
  locally (the branch was deleted on GitHub after the merge), `H` is fetched through
  `refs/pull/<n>/head`, and a still-missing `H` refuses (I9):
  1. look for the acceptance commit `A`, with the same definition readiness uses (below): the
     first commit on the first-parent path `branch_point..H` whose committed state first records
     the work item as `MILESTONE_COMPLETE`, carrying the `Workflow-Work-Item: <id>` trailer
     (TBR-R4-004). The phase at `H` alone is not enough, because a phase set by hand without
     the trailer would pass it. If no such `A` exists, a human merged before acceptance, and
     later branch commits would never reach trunk. Write `MERGED_BEFORE_ACCEPTANCE`, a refusal
     state, and gate `merged_before_acceptance`, naming its exit (`--new-pr`, "Durable
     record"). A `MILESTONE_COMPLETE` commit without the trailer is reported by name in that
     gate;
  2. after fetching `<remote>/<trunk>`, require `H` itself, not only `mergeCommit`, to be an
     ancestor of it. A true merge commit or a fast-forward satisfies this. A squash or rebase
     merge does not: the Workflow approval commits and the accepted head are missing from trunk,
     or exist there only under different SHAs, so trunk's `WORKFLOW_STATE.json` names
     `base_commit`/`reviewed_implementation_head`/approval provenance that trunk cannot reach.
     The Controller cannot undo that. It writes `MERGED_REWRITTEN` with
     `merge_method_rewrote_history` as a one-time gate naming `H` and the merge commit, does not
     switch automatically, and leaves the switch to trunk to the human. The next trunk start is
     not blocked. Otherwise, write `MERGED` with `merged_head` = `H`;
  3. from `MERGED`, require a clean tracked tree, `HEAD` on the bound branch, and the local tip
     equal to `merged_head`. A local commit that was never merged gates, and is never left
     behind silently. The gate names the commits, and its exit is to move them off the bound
     branch (for example onto a new branch, then `git reset --keep <merged_head>` on
     `milestone/<id>`); the next run re-checks, and the no-rewrite check does not apply in
     `MERGED` ("Every later preflight"). Then switch to trunk, fast-forward it to `<remote>/<trunk>` (it must be an ancestor), and
     write `CLOSED`;
  4. the step then continues to the trunk-start preflight and bare `/milestone-plan`, exactly
     as today for a repository with no active work item.

  A merge by `H` at or after `A`, including one with post-acceptance commits the human chose to
  merge, takes step 2's path. A dirty tree gates. Neither branch is deleted.

  **Close-out from trunk** (TBR-R3-001). The usual manual path is "merge on GitHub, then
  `git switch main && git pull`", which leaves the record in `PR_OPEN` or `READY` with `HEAD` on
  trunk, where step 3 cannot run. A crash in step 3 after the switch and before `CLOSED` leaves
  a `MERGED` record in the same position. Resolution rule 3 applies this reconciliation to such
  a record, with `HEAD` attached to trunk. It never switches (I4):
  0. before any record write below, the bound branch must not be checked out in any other
     worktree (the concurrency rule under "Durable record", TBR-R4-002). Otherwise refuse,
     naming that worktree, and write nothing;
  1. `PR_OPEN`/`READY`/`PR_CLOSED_UNMERGED` only: after `fetch`, `view_pr` must show the PR
     merged, with head ref, base ref and repository matching (I6). An open PR refuses, naming
     the branch (switch back to it; for a `PR_CLOSED_UNMERGED` record this is the reopen exit,
     and the branch-side preflight writes `PR_OPEN`). A PR closed without merge writes
     `PR_CLOSED_UNMERGED` (if the record is not already in it) and refuses, naming the
     refusal-state exits. Undecidable reads refuse (I9);
  2. `PR_OPEN`/`READY`/`PR_CLOSED_UNMERGED` only: steps 1 and 2 of the merged-PR handling
     above, unchanged, with `H` the PR's final `headRefOid`. They read the first-parent path
     `branch_point..H`, its committed states and trailers, and `<remote>/<trunk>`, never the
     working tree, so they are independent of which branch is checked out.
     `MERGED_BEFORE_ACCEPTANCE` refuses, and `MERGED_REWRITTEN` gates once and then never
     blocks. Otherwise write `MERGED` with `merged_head` = `H`;
  3. from `MERGED`: the local `milestone/<id>` tip, if the branch still exists, must be an
     ancestor of or equal to `merged_head` (a local commit that was never merged gates, as in
     step 3, with the same exit, or deleting the local branch), and `merged_head` must be an
     ancestor of `<remote>/<trunk>`. Then write `CLOSED`. Local trunk is not
     fast-forwarded here: the trunk-start preflight that follows gates a behind trunk and
     refuses a diverged one, as it does for any trunk start.

  From trunk, a `PR_PLANNED` record (a crash after step 2 of PR creation, then a switch to
  trunk) runs step 0, then PR creation steps 3 and 4 read-only, after `fetch`, with these
  differences: the 1-open row refuses, naming the branch, and writes nothing (adoption is
  branch-side: switch back to it); the 1-merged row is step 2 above with that PR, recorded as
  `pr`; the closed-unmerged row writes `PR_CLOSED_UNMERGED` and refuses, naming its exits; and
  the 0-match row reaches the PR-less close-out below when it applies, and otherwise refuses,
  naming the branch, and writes nothing. The refusal rows (excluded open, 2+ open, 2+ merged)
  are unchanged and name their own exits, which switching to the branch would not change
  (TBR-R7-002). Nothing is ever created from trunk.

  **PR-less close-out** (TBR-R6-001). A `BRANCH_BOUND` record whose tip is already on trunk
  never gets a PR: GitHub refuses one, and after acceptance no worker step adds a commit. This
  arises when a human merges the branch into trunk without a PR, for example after `--new-pr`
  from a post-acceptance `PR_CLOSED_UNMERGED`, or when trunk is fast-forwarded to the branch
  after a `PR_PLANNED` crash. It applies to a `BRANCH_BOUND` record, or a `PR_PLANNED` record
  whose discovery found 0 matches, when after `fetch` the tip `H` is an ancestor of, or equal
  to, `<remote>/<trunk>`, and the bound item is `MILESTONE_COMPLETE` in `H`'s committed
  `WORKFLOW_STATE.json`. `H` is the local branch tip from the branch. From trunk it is the local
  `milestone/<id>` tip if that branch exists, else `<remote>/milestone/<id>`. If neither
  exists, the preflight refuses, naming the last observed tip; the exit is to restore the
  branch there by hand (`git branch milestone/<id> <sha>`). From trunk, close-out-from-trunk
  step 0 runs first. Then:
  1. the merged-PR handling's step 1 with this `H`. No acceptance commit `A` writes
     `MERGED_BEFORE_ACCEPTANCE` with `pr` still `null`, and gates or refuses as that state does
     (its `--abandon`-only case under resolution rule 1);
  2. otherwise write `MERGED` with `pr` unchanged (`null`) and `merged_head` = `H`. Step 2's
     ancestry test holds by the condition above, so `MERGED_REWRITTEN` cannot arise here;
  3. close-out step 3 (from the branch) or close-out-from-trunk step 3 (from trunk) writes
     `CLOSED`, and the step continues to the trunk start, exactly as after a merged PR.

  No `gh pr create` call is ever made on this path. A `BRANCH_BOUND` record that is on trunk
  but not yet `MILESTONE_COMPLETE` is not this case: from the branch the worker runs, and from
  trunk it blocks, naming the branch (outcome matrix).

  **Merge method.** The policy has no merge-method field, because only history-preserving merges
  are admissible and a field with one legal value is noise. The runbook (CP10) tells the operator
  to disable "Allow squash merging" and "Allow rebase merging" in the repository's settings. That
  is a free setting, not branch protection. Step 2 is the fail-closed backstop when they are
  not disabled.

After acceptance, the work item is `MILESTONE_COMPLETE` and `active_work_item_id` is `null`, so
today's selection would fall through to bare `/milestone-plan` *on the milestone branch*. With
the policy active, the preflight resolves the work item from the binding whose branch `HEAD` is
on, before `decide` runs. The trunk-start preflight also refuses any non-trunk `HEAD`. Either
check alone prevents planning the next milestone on the old branch. Before acceptance, a bound
item missing from the branch's working tree (a plan discarded after the bind) gates
`bound_item_missing` under rule 1, so `decide`'s no-work-item path never runs on a bound branch
either (TBR-R8-001).

### Lifecycle wiring (CP8)

- `job._execute_step_locked` gains step 1b, `milestone_branch.preflight(...)`, after the state
  read and before `decide`, under the lifecycle lock. It returns:
  - `Proceed(work_item_override)`;
  - `Gate(HumanGate)`, recorded through the existing `_no_launch_record` as `GATE_BLOCKED`,
    exit 10;
  - or it raises a `ControllerError` (exit 20).

  **The no-policy probe (I1).** Deciding "no policy at `HEAD`" needs Git, because the policy is
  read from the committed tree. A working-tree existence short-circuit was rejected: a policy
  that is committed but deleted in the worktree would silently deactivate, which contradicts
  "committed tree, never working tree". The probe is a fixed budget of exactly two read-only
  `git` calls (four on an unborn `HEAD`), in this order:
  1. `git rev-parse --path-format=absolute --git-common-dir`, which gives `repo_key` for the
     binding-record lookup. That lookup is then a filesystem read;
  2. `git ls-tree -z HEAD -- .workflow-controller/policy.json`. Exit 0 with empty output means
     absent.
  3. **Unborn `HEAD` only** (TBR-R2-004): a repository with no commits, which 1.1.1 tolerates
     (`job._current_head` returns `None`), makes call 2 exit 128. On exactly that exit the probe
     makes two more read-only calls, `git rev-parse --verify -q HEAD` and
     `git symbolic-ref -q HEAD`. Exit 1 from the first and exit 0 from the second mean an unborn
     `HEAD`, which is classified as absent: no commit, so no committed policy. Any other result
     of call 2, or of these two, refuses (I9).

     This cannot classify a corrupt repository more leniently than 1.1.1 does: a lost branch
     ref gives the same two exit codes, and 1.1.1 already treats it as "no head". An orphan
     branch (`git switch --orphan`) in a repository with a committed policy is classified as
     absent too, which is I1's literal "no policy at `HEAD`". When a binding record exists,
     the resolution order still refuses it (the adopt table's catch-all row, or rule 4).

  When there is no binding record for the repository and the file is absent, the probe returns
  `Proceed(None)`. Nothing else runs, and job records, worker argv, CLI output and exit codes are
  byte-identical to 1.1.1. A binding record whose branch `HEAD` is on is governed by its own
  snapshot, even if a later commit deleted the file. The test spies on `subprocess.run` and asserts
  exactly these two argv (four on an unborn `HEAD`), plus byte-identical golden job records,
  worker argv and `inspect`/`explain` output.
- **Worker restrictions**, only when the binding is active: the worker's `--disallowedTools`
  gains `Bash(gh:*)`, `Bash(git push:*)`, `Bash(git rebase:*)`, `Bash(git switch:*)`,
  `Bash(git checkout -b:*)` and `Bash(git reset --hard:*)`, merged with `SUBAGENT_TOOLS`.
  - `git merge`, `git branch` and `git tag` are deliberately absent. Their prefixes would also
    catch the read-only `git merge-base`, `git branch --show-current` and `git tag -l` that
    Workflow commands legitimately run.
  - CP8 pins the exact list with a test, and documents the matching semantics it relies on.

  These are defense in depth only: prefix matching can be bypassed (`bash -c`). The guarantee is
  the next item.
- **Post-step verification** runs after every worker job, before the job is marked `FINISHED`:
  `HEAD` is still attached to the bound branch, and the new tip descends from the pre-step tip.
  A violation fails the job (`FAILED`, a new `BranchInvariantViolated` code). The Controller does
  not repair it.
- **Observation (I10).** The rule is one of omission, chosen so I1 holds byte for byte
  (TBR-R2-003): with no policy at `HEAD` and no binding record, every new key, object, line and
  sentence below is **omitted**, never rendered as `null` or empty. Otherwise:
  - `inspect --json` gains a top-level `repository_policy` object (path, SHA-256, enabled
    switches, trunk, forge repository), present when a policy is committed at `HEAD` or a
    binding governs the step, and a `milestone_branch` object (binding state, branch, branch
    point, PR number/URL/draft, `last_observation` freshness and drift with its timestamp),
    present when a binding governs the step;
  - `explain` names the preflight outcome it would take: bind, push, create PR, ready, a gate,
    or proceed. In `--json` form this is one new top-level `repository_preflight` key, under the
    same presence rule as `repository_policy`. It computes this from local Git and records only,
    with no fetch and no `gh`, and labels anything network-dependent "as of
    <last_observation>";
  - `status` adds one `milestone:` line per binding for the target, when one exists;
  - text output is additive. Existing JSON keys and their values are unchanged in every case.
- **`follow`** is untouched: binding events go to the binding's own `events.jsonl`, never into
  run or job event logs. A test asserts rendered `follow` output is unchanged on a
  policy-enabled fixture.
- **`milestone-binding`** (new subcommand, TBR-R4-001): the operator acknowledgement for a
  refusal-state record, `workflow-controller --work-item <id> milestone-binding --new-pr <repo>`
  or `... --abandon <repo>`, with the checks and effects stated under "Durable record". It is
  the only new subcommand. It takes the target's lifecycle lock like `step`, reuses exit codes
  0 and 20 (so the exit-code table and ADR 0001's rows are unchanged), and never launches a
  worker, creates a job record, or touches a ref or a PR. It goes through the ordinary
  `cli._dispatch` as a mutating command, so it runs pinned like `step`. That dispatch already
  creates the runtime root, writes `identity.json` and, from an unpinned source, materialises a
  snapshot, before any subcommand body runs (`controller/cli.py:1013-1062`). On a repository
  with no binding record it therefore refuses, naming the missing record, with no binding
  record, no `events.jsonl` line and no job record written; the runtime-root footprint is the
  ordinary dispatch's and nothing more (TBR-R5-003). It is never run implicitly, so it cannot
  affect I1's no-policy behaviour. `cli.ALL_COMMANDS` (`controller/cli.py:39`, pinned to the
  subparser choices by `tests/test_cli.py:121`) gains `milestone-binding`, and so does
  `tests/test_plan_document_consistency.py`'s `_COMMAND_NAMES`, so the invocation spans in this
  plan and the README are checked against the live parser. The docstring of that module's
  `extract_invocation_lines` ("one of the seven command names") becomes "eight".

### Release transaction (`controller/release_txn.py`, CP4)

**Classification** of commit `C` (the validated trunk commit), for `V = version_source(C)` and
`tag = tag_format(V)`:

1. Collect tags matching `tag_format` (inverted to versions), their peeled commits (`ls_remote`)
   and the release for `tag` (`view_release`, where "not found" is distinct and undecidable
   refuses).
2. Two ancestor tags, each possibly `None`, over the matching tags whose commit is an ancestor
   of, or equal to, `C` (TBR-R3-002):
   - `M` = the highest-versioned such tag. Only `INVALID_TRANSITION` compares with it;
   - the **baseline set** = every such tag whose version is **strictly less than `V`**, so it
     never contains `tag`. A tag is **settled** when its release is published or it is listed in
     `abandoned_tags` (TBR-R2-002). Every tag in the baseline set that is not in
     `abandoned_tags` has its release read with `view_release`, under the same rules. The
     **unsettled set** is the baseline tags that are not settled (TBR-R4-003: every lower
     ancestor tag is checked, not only the highest one). `B`, the highest tag in the baseline
     set, is kept only as a name for the examples below.
3. The state, first matching row:

| State | Condition | Action |
| --- | --- | --- |
| `COLLISION_TAG_ELSEWHERE` | `tag` exists at a commit that is not an ancestor of or equal to `C` | fail |
| `ABANDONED_VERSION` | `tag` is in the policy's `abandoned_tags`, at `C` or a strict ancestor, and no release exists for it | success, no release, with a notice naming the acknowledged tag (`v1.1.0`) |
| `ABANDONED_TAG_INCONSISTENT` | `tag` is in `abandoned_tags`, and either a release (draft or published) exists for it or the tag itself does not exist | fail (the policy contradicts the forge; an acknowledged tag is never created) |
| `ALREADY_RELEASED` | `tag` at `C`, release published, its asset set is *consistent* (below) | success, no-op |
| `RELEASE_MISMATCH` | `tag` at `C`, release published, asset set inconsistent | fail (immutable; fix with a new version) |
| `NO_CHANGE` | `tag` at a strict ancestor of `C`, release published | success, no release |
| `COLLISION_RELEASE_WITHOUT_TAG` | no `tag`, but a release named `tag` exists | fail |
| `BASELINE_UNRELEASED` | every row above failed, so either `tag` is at `C` or a strict ancestor with its release absent or draft, or there is no `tag` and no release named `tag`; and the unsettled set is non-empty (tags with no release, or only a draft, and not in `abandoned_tags`) | fail, naming every tag in the unsettled set (an interrupted release was left behind by a later version, through a bump or a hand-pushed tag) |
| `RESUME` | `tag` at `C` **or at a strict ancestor `A` of `C`**, release absent or draft (and the unsettled set empty, by the row above) | build and verify the **tag's own commit** (`C` or `A`, emitted as the `commit` output), then complete publication |
| `INVALID_TRANSITION` | no `tag`, `M` exists and `V <= version(M)` | fail |
| `RELEASE_DUE` | no `tag`, and `M` is `None` or `V > version(M)` (then the baseline set is every matching ancestor tag, all settled by the row above) | build, verify, tag, publish |

`BASELINE_UNRELEASED` now sits before `RESUME` as well as before `INVALID_TRANSITION`/
`RELEASE_DUE` (TBR-R3-002). Moving `RESUME` below `NO_CHANGE` and
`COLLISION_RELEASE_WITHOUT_TAG` changes no other outcome: `RESUME` requires `tag` with no
published release, and those two rows require a published release or no `tag`, so the three
are disjoint.

**Asset-set consistency** replaces byte comparison with a rebuild, because a rebuilt wheel is not
byte-reproducible (zip timestamps). A release's asset set is consistent when:
- its names are exactly the policy's artifacts plus the checksums file;
- the checksums file lists exactly the other assets, with matching SHA-256s;
- every artifact passes the policy's `verify` command for the **target commit**: `C`, or the
  tag's own commit for `RESUME` at a strict ancestor (TBR-R2-006). For this repository that is
  `verify-wheel --tag --commit`: BUILD_INFO, package digest, source commit, clean tree.

The plan job checks this for a published release by downloading its assets. Checking a draft
follows the same rule over the assets present. `NO_CHANGE` does not download assets: the
release at the ancestor was verified by its own run.

This rule is **desired-state, not diff-based**. It compares with the last release tag in `C`'s
history, not with `github.event.before`. A release is retried by the next trunk run that still
carries its version, whether the earlier run failed or was cancelled:
- **before the tag push**: there is no tag, so the next run is `RELEASE_DUE` at its own commit.
  That can be a later commit than the bump commit, since it carries the same version;
- **after the tag push** (for example, `create_release` got a GitHub 5xx, or someone cancelled
  the run): the tag exists at the bump commit `A` with no release, or with a draft. The next run
  at `C` is `RESUME` targeting `A`. It builds and verifies `A` itself, never `C`, because the
  published artifacts must match the tagged commit. Without this row, `v1.2.0` could silently
  become a second `v1.1.0`.

A bump followed quickly by another merge therefore cannot be skipped. Cancelling a pending run
loses nothing.

**A later bump cannot skip an interrupted release either** (TBR-R2-002). Suppose a bump to 1.2.0
at `A` pushes `v1.2.0`, `create_release` fails, and before any run resumes it the operator bumps
to 1.2.1 at `C2` (what happened after v1.1.0). The run at `C2` has no `v1.2.1`, and `B = v1.2.0`
is unsettled, so the result is `BASELINE_UNRELEASED`, not `RELEASE_DUE`. The operator resolves
it by one of two trunk commits:
- **acknowledge** `v1.2.0` in `abandoned_tags`. It is then settled, and that commit (still at
  1.2.1) is `RELEASE_DUE`;
- **resume** it: set the version back to 1.2.0. That commit is `RESUME` targeting `A` (the tag
  is at a strict ancestor, with no release). Its baseline set is the ancestor tags below 1.2.0
  (`v1.1.0`, acknowledged, and `v1.1.1`, published), so the unsettled set is empty. Once 1.2.0
  is published, the bump to 1.2.1 is `RELEASE_DUE` again.

**A hand-pushed later tag cannot skip it either** (TBR-R3-002). Same start, but the operator
bumps to 1.2.1 at `C2` and also pushes `v1.2.1` at `C2` by hand (after CP5 a tag push triggers
nothing by itself). The run at `C2` finds `tag = v1.2.1` at `C2` with no release, the `RESUME`
shape. But its baseline set contains `v1.2.0`, unsettled, so the result is
`BASELINE_UNRELEASED`. The same two resolutions apply, and with `v1.2.0` acknowledged the run is
`RESUME` for `v1.2.1`.

**Acknowledging a tag settles only that tag** (TBR-R4-003). Suppose `v1.1.5` at `A1` has no
release, a later commit carries 1.2.0, and `v1.2.0` is pushed there by hand. Its run is
`BASELINE_UNRELEASED`, naming `v1.1.5`. If the operator acknowledges `v1.2.0` instead, the
1.2.1 bump is still `BASELINE_UNRELEASED`: its baseline set holds `v1.1.5` as well as `v1.2.0`,
and `v1.1.5` is unsettled. Nothing is skipped until `v1.1.5` itself is resumed or acknowledged.

No induction is needed. Both publishing rows, `RESUME` and `RELEASE_DUE`, check every lower
matching ancestor tag directly, so no release this transaction publishes has an unsettled
lower ancestor tag. The cost is one `view_release` per lower ancestor tag that is not
acknowledged: one per past release, which is two reads for this repository today. The only
pre-existing unreleased tag, `v1.1.0`, is acknowledged in the reference policy. So every tag
without a release is either resumable (`RESUME`, its version still current and every lower tag
settled) or a hard stop (`BASELINE_UNRELEASED`, for any higher version, whether bumped or
hand-tagged), unless it is acknowledged. The rows that publish nothing
(`ALREADY_RELEASED`, `NO_CHANGE`) do not re-check the baseline. They can only be reached above
an unsettled tag through a release published by hand, outside this transaction, and that is an
operator taking over publication, which this guarantee does not cover. The one tag that deliberately has no release is acknowledged
explicitly, through `abandoned_tags`, never inferred, and never skipped over. `NO_CHANGE` is the ordinary merge case. For today's `main`,
`V = 1.1.1`, `v1.1.1` is at `00c3b71`, an ancestor, and published, so the result is `NO_CHANGE`.
`v1.1.0` only matters as an ancestor tag (settled as a baseline, because it is acknowledged) for
`BASELINE_UNRELEASED` and `INVALID_TRANSITION`, and as `ABANDONED_VERSION` when a historical
commit carrying 1.1.0 is replayed (CP9 scenario 5).

**Transaction** (publish job; state recomputed *immediately before* acting, with a fresh tag
fetch and fresh `gh` reads, never trusting the plan job's output). The target commit is `C` for
`RELEASE_DUE`, and the tag's own commit for `RESUME`. A recomputed target that differs from the
commit the build job built refuses:
1. Download the built artifacts, re-run the policy's `verify` command on each, and recompute
   `SHA256SUMS`.
2. `RELEASE_DUE` only:
   - `create_annotated_tag(tag, C, "<publication notes>")`, with a committer identity of
     `github-actions[bot]`;
   - `push_tag`. If the push is rejected, reclassify: `RESUME` at `C` means another run created
     the tag at `C`, so continue; anything else fails.
3. Verify with `ls_remote` that `tag` peels to the target commit.
4. Publish:
   - release absent: `create_release`;
   - draft: download the present assets. Every one must have an expected name, and every
     present artifact must pass `verify`. Present artifacts are authoritative. Missing artifacts
     are filled from this run's build. A missing checksums file is computed over the final
     artifact bytes. A present checksums file must match them. Then `upload_assets` the missing
     ones and `publish_draft`.

   A draft that fails this check fails the job, for a human to inspect. The Controller never
   deletes it.
5. Post-publication verification: `view_release` must show the release not draft, and the
   downloaded asset set must be consistent.

Moving or deleting a tag is not a possible action (I2, I8). Every failure after step 2 is
resumed at the tagged commit, by the next trunk push or by `workflow_dispatch` at the trunk tip,
since both classify as `RESUME` targeting the tag. It no longer depends on GitHub's time-bounded
"re-run" of the original run.

### CI/CD (CP5)

Rendered by `tools/ci_workflows.py`, still the single model, with `--check` and a test:

- **`validate.yml`**: unchanged jobs and matrices (`fail-fast: false`), `workflow_call`
  only, major-tag actions (ADR 0002's decision stands: no validate job holds a write token).
  One addition: a `trunk` shard for the new test modules.
- **`ci.yml`**: `on: pull_request` only. Concurrency `${{ github.workflow }}-${{ github.ref }}`
  with `cancel-in-progress: true`, calling `validate.yml`. Milestone branches are validated
  through their Draft PR.
- **`main.yml`** (new): `on: push: branches: [main]` and `workflow_dispatch`, with no
  inputs. The dispatch classifies the current trunk tip. Because `RESUME` targets the tag's own
  commit, that also resumes an interrupted release whose tag sits at an earlier commit, so an
  input naming a commit or tag is unnecessary. A job condition requires
  `github.ref == 'refs/heads/main'`. Concurrency group `main-release` with
  `cancel-in-progress: false`. With that setting GitHub holds one running and one *pending* run
  per group, and a newer pending run cancels the older pending one. A burst of three merges can
  therefore skip the middle commit's run entirely. That is safe under desired-state
  classification: a running publish is never cut short, and a skipped release lands in a later
  run at a later commit carrying the same version (or at the tag's commit, via `RESUME`). The
  release commit can thus be later than the bump commit. Top-level
  `permissions: contents: read`. Jobs:
  - `validate`: `uses: ./.github/workflows/validate.yml`;
  - `release-plan` (needs `validate`, read-only, with `GH_TOKEN: ${{ github.token }}` under
    the read-only top-level permissions, because classification calls `view_release` and
    downloads assets for `ALREADY_RELEASED`): checkout with `fetch-depth: 0` and tags, then
    `python3 tools/release.py classify --commit "$(git rev-parse "$GITHUB_SHA^{commit}")"`. Its
    outputs are `state`, `version`, `tag` and `commit`, where `commit` is the target commit
    (the tag's own commit for `RESUME`). A failing state fails the job;
  - `build` (needs `validate` and `release-plan`, when `state` is `RELEASE_DUE` or `RESUME`,
    SHA-pinned):
    1. check out exactly `commit`;
    2. `tools/release.py build`, which runs the policy's `build` command;
    3. `tools/release.py verify`, which runs the policy's `verify` command;
    4. the pipx smoke test (unchanged);
    5. `checksums`, then upload the artifact;
  - `publish` (needs all three, the same condition, SHA-pinned; the only job with
    `permissions: contents: write`, and `GH_TOKEN: ${{ github.token }}`): download the artifact,
    then `tools/release.py publish --commit "$COMMIT"`, which runs the transaction above;
  - a pull request run never reaches `release-plan`.
- **`release.yml`** is deleted, so pushing a `v*` tag by hand no longer triggers anything.
- The rule that non-write jobs use `persist-credentials: false` extends to `validate`,
  `release-plan` and `build`.

### Compatibility and migration

- The installed Controller 1.1.1 stays the orchestrator of this milestone. It never reads
  `.workflow-controller/`, the new modules, or `main.yml`. No test depends on the installed
  Controller, and nothing here runs the source checkout as its own orchestrator.
- The Workflow admission set is unchanged: `managed_repo.VALIDATED_WORKFLOW_RELEASES` stays
  `{"2.5.1"}` (`controller/managed_repo.py:89`) and `SUPPORTED_WORKFLOW_LINE` stays a single line.
  This repository stays on the installed Workflow 2.5.1 for the whole milestone.
- `controller/GENERATION.json` stays `1`. Job-record schema is unchanged. Binding records are
  new files under a new `repositories/` subdirectory of the runtime root, and a 1.1.1 runtime
  ignores them. Handoff semantics are unchanged.
- Packaged-runtime isolation: all new modules are stdlib-only and ship in the wheel.
  `test_packaged_runtime`'s rename/delete-checkout cases gain a policy-enabled target, to prove
  a packaged runtime never needs its source checkout for branch/PR work.
- Other repositories (RepFlow, workflow-manager) are unaffected until they commit a policy file.
  Adopting one later needs:
  1. a policy file;
  2. their CI calling the same `controller.release_txn` through a Controller installed in CI;
  3. for Workflow-managed repositories, classifying `.workflow-controller/` in whichever work
     item introduces the file.

  Adoption tooling for them is not built here.

### This milestone's own rollout

1. This milestone is implemented on `main`, with no push by Claude.
2. After acceptance, the operator installs the new Controller locally only for disposable trials
   (`CP10`'s runbook). The installed release stays 1.1.1 until 1.2.0 is published.
3. The first automatic release is a separate post-acceptance commit that sets
   `version = "1.2.0"`. Its push to `main` is the first real `RELEASE_DUE` run. The runbook
   includes a pre-push local `tools/release.py classify` against the fetched remote. It also has
   a one-time check that the repository's immutable-releases setting is on.
4. The next milestone after that is the first one executed on `milestone/<id>` with a Draft PR.

None of these steps waits for Workflow 2.6.x. Acceptance follows CP10, and the 1.2.0 release and
the first dogfooded milestone run under Workflow 2.5.1 with the fail-closed
`integration_required` gate. The follow-up integration milestone is sequenced after both this
milestone and the Workflow Manager 2.6 milestone complete, and it is independent of steps 2-4.

## Checkpoints

<!-- generated by workflow_state.render_registry_markdown(registry) -- do not edit by hand -->
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Single Controller version authority: static [project].version in pyproject.toml, controller/version.py reduced to a stdlib-only resolver (source runtime reads its own code root's pyproject.toml, package runtime reads the owning distribution's metadata cross-checked against BUILD_INFO.json, never importlib.metadata for a source runtime), setup.py/identity/handoff/cli/tools rewired, version agreement tests | - | 3 | 1 |
| CP2 | Generic repository policy: controller/repo_policy.py (schema v1, strict validation, closed adapter registries for version source/version scheme/tag format/build+verify command/artifacts/publication, milestone-branch section), read from a committed tree never the working tree, explicit activation, and this repository's reference .workflow-controller/policy.json | CP1 | 3 | 1 |
| CP3 | Target-repository Git and GitHub boundaries: controller/gitrepo.py (fetch, ancestry, no-force branch create/switch, fast-forward-only push, no-force annotated tag create/push, ls-remote peel, the history-preserving trunk-integration merge primitive) and controller/forge.py (the gh adapter: repository identity, PR list/view/create-draft/ready, release view/create/upload/publish-draft/download, no merge operation, undecidable reads refuse), plus the executable fake gh and bare-origin fixtures | - | 4 | 1 |
| CP4 | Generic release transaction: controller/release_txn.py classification (NO_CHANGE, RELEASE_DUE, RESUME, ALREADY_RELEASED, and the fail-closed collision/invalid-transition states), tag-after-validation, publish and draft-resume without ever moving a tag, post-publication asset verification; tools/release.py rewritten as the reference adopter's thin CLI | CP2, CP3 | 4 | 1 |
| CP5 | CI/CD: tools/ci_workflows.py renders main.yml (push to main and workflow_dispatch: validate, release-plan, build, publish as one transaction; no cancellation; write permission only in publish), ci.yml (pull requests, same-ref cancellation), validate.yml unchanged in jobs, release.yml removed; test_ci_workflows rewritten | CP4 | 3 | 1 |
| CP6 | Milestone branch binding: controller/milestone_branch.py durable binding records under the runtime root keyed by the target's Git common directory, persist-before-act creation of milestone/<work-item-id> from the trunk tip, the adopt/collision decision table, crash reconciliation at every persist boundary, policy snapshot at bind time, trunk-start preflight and fast-forward-only branch sync | CP2, CP3 | 4 | 1 |
| CP7 | Draft PR lifecycle and completion: PR discovery/creation/reuse with identity verification, trunk-drift detection, the Workflow 2.5.1 integration-required gate, readiness preconditions and gh pr ready at MILESTONE_COMPLETE, the human-only merge gate, merged/closed-PR handling, the refusal-state exits (reopen, new PR, abandon), and fast-forward close-out to trunk | CP6 | 4 | 1 |
| CP8 | Lifecycle wiring: the repository preflight under the lifecycle lock in job._execute_step_locked, worker tool restrictions and post-step branch verification when the policy is active, branch/PR/drift/release-policy blocks in inspect/explain/status, the milestone-binding acknowledgement subcommand, byte-identical behaviour with no policy, follow unchanged | CP7 | 4 | 1 |
| CP9 | End-to-end disposable scenarios: policy-enabled lifecycle from trunk to a ready Draft PR and human merge with fake claude, fake gh and a bare origin; interruption and restart at every persist boundary; trunk drift; no-policy regression; release classification over a disposable trunk history | CP4, CP8 | 3 | 1 |
| CP10 | Operator documentation and full verification under Workflow 2.5.1 (terminal checkpoint): README (milestone branches, pull requests, releasing rewritten), ADR 0003, ROADMAP entry including the follow-up Workflow 2.6.x integration milestone, the first-automatic-release runbook, full suite, packaged runtime, ci_workflows --check | CP5, CP9 | 2 | 1 |


Every checkpoint ends with the full Controller suite green and `tools/ci_workflows.py --check`
passing. It is committed by `/milestone-implement` in the usual per-checkpoint commit.

### CP1 -- single version authority

Files:
- `pyproject.toml`, `controller/version.py`, `controller/identity.py`, `controller/handoff.py`,
  `controller/cli.py`, `setup.py`, `tools/release.py` (`main()` reads
  `version.source_version(<repository root>)` for `version`, `verify-tag` and `verify-wheel`, and
  `verify-tag` requires the static form);
- tests: `tests/fixtures.py` (`build_package_tree`, `build_checkout`), `tests/test_buildinfo.py`,
  `tests/test_identity.py`, `tests/test_handoff.py`, `tests/test_cli.py`,
  `tests/test_release_tools.py`, `tests/test_packaged_runtime.py` (`_write_version` rewrites
  `pyproject.toml`), plus the new `tests/test_version_authority.py`.

Tests:
- `pyproject.toml` has a static version and no dynamic version;
- no `__version__` assignment remains anywhere in `controller/`;
- the source runtime reports the `pyproject.toml` version even when:
  - a stale `workflow_controller.egg-info` with another version sits in the checkout;
  - another `workflow-controller` distribution is importable on `sys.path`;
- the package runtime reports the wheel's metadata version;
- metadata/BUILD_INFO disagreement makes the runtime `unidentified`;
- a pinned child reports its pin's version, and `pin()` refuses a `SOURCE_PIN.json` with a
  missing or non-semver `version` (no fallback);
- a package-kind pinned child reports its snapshot's `controller/BUILD_INFO.json` version;
- a dirty source snapshot (`--allow-dirty-source`) whose `pyproject.toml` version differs from
  the parent's reports the snapshot's version;
- `--version` line 1, `METADATA` `Version`, `BUILD_INFO.version` and `tools/release.py version`
  are all equal (packaged test case 6, extended);
- handoff reads `HEAD:pyproject.toml`;
- `tools/release.py version`, `verify-tag` and `verify-wheel` take the version from the
  checked-out `pyproject.toml`: a test rewrites it in a disposable checkout and sees all three
  follow.

### CP2 -- repository policy

Files:
- `controller/repo_policy.py` (new; `DEPENDENCY_ORDER` after `errors`);
- `controller/errors.py` (`InvalidRepositoryPolicyError`);
- `controller/__init__.py`;
- `.workflow-controller/policy.json` (new, the reference content above);
- `tests/test_repo_policy.py`, `tests/test_package_structure.py`.

Tests:
- the reference file validates;
- every rule above refuses, each with its own case: unknown key, schema, placeholder, ref
  validity of branch and tag formats over the id alphabet, non-invertible tag format, unknown
  adapter kind, dynamic pyproject version, an `abandoned_tags` entry that does not render from
  `tag_format`;
- the reader uses `git show HEAD:` and ignores working-tree edits;
- absent file means disabled.

### CP3 -- Git and GitHub boundaries

Files:
- `controller/gitrepo.py`, `controller/forge.py` (new);
- `controller/errors.py` (`GitOperationError`, `ForgeError`, `ForgeUndecidableError`);
- `tests/fake_gh.py` (new, executable);
- `tests/fixtures.py`: `build_origin_pair(tmp)`, a bare origin plus a clone with `main`, and
  `fake_gh_env(...)`;
- `tests/test_gitrepo.py`, `tests/test_forge.py`, `tests/test_no_rewrite_invariants.py` (the
  static scans for I2/I3).

Tests:
- fast-forward-only push refuses a diverged remote without contacting it again;
- a tag push refuses an existing tag, including an existing tag at the same commit, which is
  then read as "exists";
- `merge_trunk` produces a two-parent merge whose first parent is the branch, and on conflict
  aborts and leaves the tree unchanged;
- `worktree_branches()` reports the branch checked out in each of two linked worktrees of one
  fixture repository, and a detached one as no branch (TBR-R4-002);
- every `gh` failure class maps to `ForgeUndecidableError`;
- `pr_checks` classifies exit 8 with JSON as pending, exit 1 with JSON as a failing check, and
  exit 1 with the "no checks reported" message as `no_checks`. Exit 8 or 1 without parseable
  JSON is `ForgeUndecidableError`;
- `pr_checks` accepts each of the five documented buckets, including `cancel`, and a check with
  an out-of-set or missing `bucket` is `ForgeUndecidableError` (TBR-R3-003);
- `--repo` is present on every call;
- the forge static scan catches `"gh", "api"` and `"pr", "merge"` argv literals in a planted
  fixture module, and does not flag the identifier `api` or prose;
- the fake records no `pr merge` in any test run, via a suite-level assertion in the e2e
  module.

### CP4 -- release transaction

Files:
- `controller/release_txn.py` (new);
- `tools/release.py`: new subcommands `classify`, `build`, `verify` and `publish`, next to
  `version`, `verify-wheel` (unchanged checks) and `checksums`. `verify-tag`, `check-unpublished`
  and `verify-tag-commit` **stay** until CP5, because the committed `release.yml` calls all
  three. CP5 deletes them together with `release.yml`, so no interval between checkpoints leaves
  a `v*` tag push half-supported;
- `tests/test_release_txn.py` (new), `tests/test_release_tools.py`.

Tests: every classification row, each over a disposable history with a bare origin and the fake
`gh`:
- a version bump means `RELEASE_DUE`;
- no bump means `NO_CHANGE`;
- the `v1.1.0` shape, listed in `abandoned_tags` (orphan tag without release): classifying the
  commits that carry 1.1.0 gives `ABANDONED_VERSION`, and later 1.1.1 commits give `NO_CHANGE`;
- an interrupted release whose tag already landed (tag for `V` at the bump commit `A`, then
  `create_release` fails with a 5xx or the run is cancelled, then a later merge `C`): the run at
  `C` is `RESUME` targeting `A`, **not** a silent `NO_CHANGE`. It builds and verifies `A`, and
  publishes the release with the tag unchanged. The same holds for a draft left at `A`, and for
  a `workflow_dispatch` at the trunk tip;
- an `abandoned_tags` entry with a release, or with no tag, means `ABANDONED_TAG_INCONSISTENT`;
- an interrupted release left behind by a later bump (`v1.2.0` at `A` with no release, then a
  bump to 1.2.1 at `C2`): the run at `C2` is `BASELINE_UNRELEASED`, naming `v1.2.0`, **not**
  `RELEASE_DUE`. The same holds when `v1.2.0` has only a draft release. The acknowledged
  variant (`v1.2.0` in `abandoned_tags`) gives `RELEASE_DUE` for 1.2.1. The resume variant (a
  commit setting the version back to 1.2.0) gives `RESUME` targeting `A`, and after publication
  the 1.2.1 commit gives `RELEASE_DUE`;
- a hand-pushed later-version tag over an unreleased lower tag (`v1.2.0` at `A` with no release,
  then a bump to 1.2.1 at `C2` with `v1.2.1` pushed at `C2` by hand, no release): the run at
  `C2`, and at every later 1.2.1 commit, is `BASELINE_UNRELEASED` naming `v1.2.0`, **not**
  `RESUME`. With `v1.2.0` in `abandoned_tags` it is `RESUME` targeting `C2` (TBR-R3-002);
- the resume commit restoring 1.2.0 has a baseline set of `v1.1.0` (acknowledged) and `v1.1.1`
  (published), both settled, and never contains `v1.2.0` itself;
- an acknowledged higher tag does not settle a lower one (TBR-R4-003): `v1.1.5` with no release,
  then `v1.2.0` pushed by hand on a 1.2.0 commit and listed in `abandoned_tags`. A later 1.2.1
  commit is `BASELINE_UNRELEASED`, naming `v1.1.5`, **not** `RELEASE_DUE`. With `v1.1.5` also
  acknowledged it is `RELEASE_DUE`. With two unsettled lower tags, the failure names both;
- an acknowledged tag in the baseline set is never read with `view_release` (the fake `gh`
  invocation log shows one `release view` per non-acknowledged lower ancestor tag);
- a lower version means `INVALID_TRANSITION`;
- a tag on a side branch means `COLLISION_TAG_ELSEWHERE`;
- a release without a tag means `COLLISION_RELEASE_WITHOUT_TAG`;
- a published release with a wrong asset means `RELEASE_MISMATCH`;
- `RESUME` at a strict ancestor `A` runs the policy's `verify` with `--commit A`, never `C`.

Transaction cases:
- interrupted after the tag push: rerun gives `RESUME`, then a published release, and the tag
  commit is unchanged;
- interrupted mid-upload (draft holding only the wheel): resume keeps that wheel, computes
  `SHA256SUMS` over *its* bytes, uploads it and publishes;
- a draft holding only `SHA256SUMS` whose hash disagrees with this run's rebuilt wheel fails,
  and the draft is untouched;
- a draft with a foreign or unverifiable asset fails, and the draft is untouched;
- a concurrent tag creation at `C` (push rejected) continues;
- a concurrent tag at another commit fails.

Across all cases, no argv ever contains `--force`, `--clobber`, `delete` or `-f`.

### CP5 -- CI/CD

Files:
- `tools/ci_workflows.py`: the model for `main.yml` and `ci.yml`, the removal of `release.yml`,
  and the `trunk` shard;
- `tools/release.py`: remove `verify-tag`, `check-unpublished` and `verify-tag-commit` (their
  only caller, `release.yml`, goes in this checkpoint);
- `.github/workflows/{main,ci,validate}.yml` (rendered);
- `.github/workflows/release.yml` (deleted);
- `tests/test_ci_workflows.py` (rewritten release section).

Asserted:
- triggers;
- concurrency, where pull requests cancel and `main-release` never cancels;
- every matrix is `fail-fast: false`;
- `contents: write` appears only on `publish`, and the top level is read-only;
- `release-plan` carries `GH_TOKEN: ${{ github.token }}` and no write permission;
- `workflow_dispatch` has no inputs;
- `persist-credentials: false` on non-write checkouts;
- every action in `build`/`publish` is SHA-pinned;
- the needs chain: `publish` needs `validate`, `release-plan` and `build`;
- the `build`/`publish` conditions reference the plan's `state` output;
- `publish` calls `tools/release.py publish` with the peeled commit;
- no step creates a tag outside `publish`;
- no `--clobber` or `delete`;
- `release.yml` is absent, and `tools/release.py` no longer offers the three tag-first
  subcommands;
- `workflow-conformance.yml` still matches `installation.json`.

### CP6 -- milestone branch binding

Files:
- `controller/milestone_branch.py` (new: the record, bind, adopt/collision, trunk-start
  preflight, sync);
- `controller/errors.py` (`BranchBindingError`, `BranchInvariantViolatedError`);
- `controller/runtime.py` (the `repositories/` subdirectory constant only, if needed);
- `tests/test_milestone_branch.py` (new).

Tests:
- fresh creation from `main` with an uncommitted plan carried over;
- every adopt/collision row;
- a crash injected after each persist boundary (`BRANCH_PLANNED` before `switch -c`, after
  `switch -c` before `BRANCH_BOUND`), after which a restart converges to exactly one branch with
  `HEAD` on it;
- a rewritten branch (reset behind the recorded tip) refuses;
- a remote ahead-and-diverged branch refuses;
- a policy snapshot edited on the branch does not change the binding's behaviour;
- adopt with the policy edited on the milestone branch uses the branch-point policy read at
  `P = merge-base(<remote>/<trunk>, HEAD)`;
- adopt mid-implementation (lost runtime root, `plan_approval` set, approval commit on the
  branch) succeeds. With the approval commit on trunk, or `base_commit` not an ancestor of `P`,
  it refuses;
- the bind step admits exactly the enumerated plan-stage phases and refuses `AMENDING_PLAN`;
- the catch-all row: record `BRANCH_PLANNED`, branch absent, and a bind-step precondition
  failing at the current trunk tip refuses and names the observation. With trunk moved past `T`
  and every bind-step precondition holding, the bind re-runs at the new tip and rewrites `T`.
  With the branch at a descendant of `T` and `HEAD` on it, the record completes to
  `BRANCH_BOUND` (TBR-R6-001, outcome matrix);
- the `BRANCH_PLANNED` catch-all's exits (TBR-R7-001):
  - (a) a crash between `switch -c` and the `BRANCH_BOUND` write, a commit on the branch, then
    `git switch main`: the refusal names `milestone/<id>` and says to switch to it; after the
    switch the next preflight completes the record to `BRANCH_BOUND`;
  - (b) a `milestone/<id>` created by hand at a commit that does not descend from `T`: the
    refusal says to remove or rename it; after its removal the re-bind row applies;
  - (c) a crash before `switch -c`, then the plan approved on trunk (`plan_approval` committed
    on local trunk): the refusal names both exits of (c); after `git switch -c milestone/<id>`
    at the trunk tip the next preflight completes the record to `BRANCH_BOUND`. The same with
    trunk rewound below `base_commit` completes after the branch is created at `T`. With the
    plan discarded from trunk's working tree, `milestone-binding --abandon` writes `ABANDONED`
    and the next trunk start proceeds; with the plan still present it refuses, naming the
    create-and-switch exit, and writes nothing. The (c) refusal names `--abandon` only when its
    precondition holds, and create-and-switch otherwise, never both. With the plan discarded
    only in the working tree while the approval commit is still in local trunk's `HEAD`,
    `--abandon` refuses and writes nothing (TBR-R8-005);
  - the (b) fixture asserts the outcome of the row that matches first (TBR-R8-002): a local
    `milestone/<id>` not descending from `T`, with `HEAD` on trunk and with `HEAD` on it, and a
    remote-only one, each match the branch-not-descending row, whose refusal names exit (b). A
    `BRANCH_PLANNED` record whose recorded forge repository differs from the repository's
    refuses, naming both values and the exit, and one whose recorded policy snapshot differs from
    the policy at the current trunk tip is not refused for that reason;
- a plan discarded after a completed bind (TBR-R8-001): `/milestone-plan` on trunk, a
  successful bind (record `BRANCH_BOUND`, `HEAD` on `milestone/<id>` at `branch_point`), then
  `git checkout -- . && git clean -fd` of the plan-stage files:
  - from the branch, the step gates `bound_item_missing`, naming the item, the branch and both
    exits, and launches no worker (bare `/milestone-plan` is never selected on the branch);
  - from trunk (after `git switch main`), the refusal names the branch and both exits:
    `milestone-binding --abandon` if the plan was discarded, or switch back to the branch and
    restore it if it was stashed (`O3`). The same fixture with the plan stashed
    (`git stash -u`) instead of discarded gives the identical refusal, and switching back and
    `git stash pop` lets the step on the branch proceed;
  - `--abandon` from either side writes `ABANDONED`. From the branch, the next step gates
    `switch_to_trunk`. From trunk, the next trunk start selects bare `/milestone-plan` on trunk;
  - with the plan restored (`git stash pop`), the step on the branch proceeds;
  - `--abandon` refuses, naming the restore exit, when the branch tip is past `branch_point`,
    when `<remote>/milestone/<id>` exists, or when the working tree or `HEAD`'s committed state
    still has a non-terminal entry for the item. It also refuses on a record with a non-empty
    `superseded_prs`, and on one whose `last_observation.tip` differs from `branch_point`, even
    with the local tip back at `branch_point` and no remote branch (`O2`);
  - re-planning the abandoned id (`O4`): after `--abandon`, `/milestone-plan` on trunk derives
    the same id while the leftover local `milestone/<id>` still sits at `T`. The bind refuses,
    naming the branch and the exit `git branch -d milestone/<id>`, and writes nothing. After the
    deletion the bind renames the `ABANDONED` record aside and proceeds;
  - the same gate for a `BRANCH_PLANNED` record with `HEAD` on the branch at `T` and the plan
    discarded: the record first completes to `BRANCH_BOUND`, and then the gate names
    `--abandon`;
- a remediation child (created with `create_remediation_child_work_item` on a bound parent's
  branch, `parent_work_item_id` set, `base_commit` at the parent's implementation head) is
  resolved by rule 1. Its steps proceed on the parent's branch under the parent's binding, with
  no second binding record, no second branch and no bind or adopt attempt. With `HEAD` on trunk,
  a child is never the bind candidate;
- work-item resolution: two unbound top-level non-terminal items with `HEAD` on trunk refuse,
  naming both; a bound non-terminal item with `HEAD` on trunk refuses, naming its branch;
- from trunk after the plan approval commit is on the branch (TBR-R3-001): milestone `X` bound
  and `IMPLEMENTING` on `milestone/X` with record `PR_OPEN`, then a clean `git switch main`, so
  trunk's `WORKFLOW_STATE.json` has no entry for `X`. The preflight refuses and names
  `milestone/X`, and bare `/milestone-plan` is never selected. The fixture is built past the
  plan-stage window on purpose. The same holds for records in `BRANCH_BOUND` and `READY`;
- a `PR_CLOSED_UNMERGED` record (PR still closed) and a `MERGED_BEFORE_ACCEPTANCE` record each
  refuse from trunk, naming the branch and their exits, while `CLOSED`, `ABANDONED` and
  post-gate `MERGED_REWRITTEN` records do not block the trunk start. The state classification
  itself is pinned: a test enumerates all eleven states and asserts each is in exactly one of
  non-terminal, refusal and terminal (TBR-R4-001);
- an `ABANDONED` record with `HEAD` switched back to its leftover `milestone/<id>` gates with
  `switch_to_trunk` and never reaches the adopt table. The same id planned again on trunk is a
  bind candidate. The bind refuses while the old local or remote branch exists. Once both are
  gone, it renames the old record to `<id>.abandoned-1.json` and binds with
  `binding_generation` 2. A crash between the rename and the create converges to one new record,
  also with `binding_generation` 2. The `events.jsonl` lines of both bindings are in the one log,
  each naming its generation (TBR-R5-002);
- the transition table is pinned: a test enumerates every (from, to) pair over the eleven states
  plus "no record", and asserts that the record writer accepts exactly the table's pairs, while
  every other pair raises `BranchBindingError` and writes nothing (TBR-R5-004). Same-state
  rewrites, including `MERGED_REWRITTEN`'s one-time gate flag, are accepted;
- rule 1 with a terminal binding (`CLOSED`, and `MERGED_REWRITTEN` after its gate) and `HEAD`
  still on its branch gates with `switch_to_trunk`, and neither PR discovery nor readiness runs
  (TBR-R3-004);
- adopt after acceptance (lost runtime root, work item `MILESTONE_COMPLETE`, acceptance commit
  `A` in `P..HEAD`) succeeds and continues to PR discovery. The same shape with `A` missing or
  on trunk refuses.

### CP7 -- Draft PR lifecycle and completion

Files:
- `controller/milestone_branch.py` (PR, drift, readiness, merge gate, close-out);
- `controller/decision.py`: the new `HumanGate` texts (every gate this plan names, including
  `checks_cancelled`, `switch_to_trunk`, `pr_closed_unmerged`, `merged_before_acceptance` and
  `bound_item_missing`);
  `SELECTED_COMMANDS` and the dispatch table are unchanged;
- `tests/test_pull_request_lifecycle.py` (new).

Tests:
- PR creation only after the first branch commit;
- reuse after restart, including a crash between `gh pr create` and the record write, with no
  duplicate;
- refusal on 2 open PRs (seeded directly into `fake_gh`'s state, TBR-R6-003), 2 non-excluded
  merged PRs (naming the lost-runtime-root recovery), a cross-repository PR, a base mismatch, or
  a PR number whose head changed;
- `fake_gh pr create` refuses a head and base that already has an open PR (TBR-R6-002). After
  `--new-pr` from `PR_CLOSED_UNMERGED` and a human reopening the superseded PR, the next
  preflight refuses, naming that PR as superseded and saying to close it, and makes no
  `gh pr create` call. Once it is closed on the fake forge, the next preflight creates exactly
  one new Draft PR;
- the outcome matrix under "Durable record" is pinned (TBR-R6-001): a parametrised test builds one fixture per matrix
  cell, from the branch and from trunk, runs one preflight, and asserts that the outcome is the
  cell's named writer, gate or refusal, and that the gate or refusal names the cell's exit.
  A test also asserts that every classified state has at least one row and that a split
  state's rows partition it, so a state added without a row fails (TBR-R7-004). The trunk-side
  `PR_PLANNED` cells are one fixture per discovery row (TBR-R7-002): excluded open, 2+ open and
  2+ merged each refuse, naming their own exit and not "switch to it"; closed-unmerged writes
  `PR_CLOSED_UNMERGED`; 1 merged writes `MERGED`; 1 open refuses, naming the branch; and a
  0-match row that does not qualify for the PR-less close-out (trunk fast-forwarded before
  acceptance) refuses, naming the branch, and writes nothing. Every trunk-side `BRANCH_BOUND`
  cell and the `PR_PLANNED` 0-match row, with `milestone/<id>` deleted locally and on the
  remote, refuse, naming the last observed tip (TBR-R7-004);
- the PR-less close-out (TBR-R6-001):
  - after acceptance the PR is closed unmerged, `--new-pr` returns the record to
    `BRANCH_BOUND`, and trunk is fast-forwarded to `A` on the bare origin. The next step on the
    branch writes `MERGED` with `pr == null` and `merged_head == A`, then `CLOSED`, switches to
    `main`, and continues to the trunk start, with no `gh pr create` call and no worker launch;
  - the same record with `HEAD` on trunk (after a hand `git switch main && git pull`) converges
    to `CLOSED` from trunk, without a switch; the same with the local and remote branch both
    deleted refuses, naming the last observed tip;
  - a `PR_PLANNED` record with no PR and trunk fast-forwarded to `A` converges to `CLOSED`, from
    the branch and from trunk, with no create call;
  - trunk fast-forwarded to a tip whose `MILESTONE_COMPLETE` commit lacks the
    `Workflow-Work-Item` trailer writes `MERGED_BEFORE_ACCEPTANCE` with `pr == null`; its gate
    names `--abandon` alone, `--abandon` passes its precondition, skips the PR re-read, and the
    next trunk start proceeds;
  - a `BRANCH_BOUND` record on trunk but not `MILESTONE_COMPLETE` still launches the worker from
    the branch and still blocks from trunk, naming the branch;
- close-out step 3's unmerged-commit gate: after the gate, moving the commit to a new branch
  and resetting `milestone/<id>` to `merged_head` lets the next run reach `CLOSED`, with no
  rewrite refusal;
- a human-readied PR is not flipped back;
- a closed-unmerged PR gates;
- drift informational before completion and an `integration_required` gate at readiness;
- `checks_pending` (including "no checks reported") and `checks_failing` gates;
- a `cancel` bucket from `fake_gh` gives the `checks_cancelled` gate naming the check, and a mix
  of buckets follows the stated precedence (TBR-R3-003);
- refusal-state exits (TBR-R4-001), each through the `milestone_branch` API that CP8's
  subcommand calls:
  - reopen: a `PR_CLOSED_UNMERGED` record, then the fake `gh` reopens the PR. The next
    branch-side preflight writes `PR_OPEN` and continues, with no new PR. A reopened PR whose
    head ref changed refuses (I6). A reopened-then-merged PR goes to the merged-PR handling.
    A reopened PR on a branch whose working tree has lost the bound item's state entry writes
    `PR_OPEN` and then gates `bound_item_missing` in the same preflight, with no readiness or
    worker step (`O1`).
    From trunk, a reopened PR refuses, naming the branch, and writes nothing;
  - `--new-pr` from `PR_CLOSED_UNMERGED`: the record returns to `BRANCH_BOUND` with the old
    number in `superseded_prs`. The next preflight creates exactly one new Draft PR and never
    adopts the superseded one;
  - `--new-pr` from `MERGED_BEFORE_ACCEPTANCE` with the branch tip equal to `H` (TBR-R5-001):
    the record returns to `BRANCH_BOUND`, and the next step makes **no** `gh pr create` call,
    leaves the record in `BRANCH_BOUND`, and launches the worker. The step after the next branch
    commit creates exactly one new Draft PR, and its readiness gates `integration_required`.
    `fake_gh` rejects a create with no commit beyond the base, so a creation attempt at `H`
    would fail this test;
  - re-bind of an abandoned id through to the first PR (TBR-R5-002): `fake_gh` keeps the
    abandoned binding's PR (closed, unmerged, head `milestone/<id>`). After the old branches are
    deleted, the re-plan binds, and the new plan approval commit lands, the next preflight
    creates exactly one new Draft PR and never matches, adopts or refuses on the old one. The
    same holds when the process is killed between the rename and the create;
  - PR discovery in `PR_PLANNED` with this binding's own PR closed before the record write
    writes `PR_CLOSED_UNMERGED` and gates, and a later `--new-pr` continues. A human
    fast-forward of trunk to the branch tip after `PR_PLANNED` returns the record to
    `BRANCH_BOUND` with no create call;
  - `--abandon` from `PR_CLOSED_UNMERGED` writes `ABANDONED` and appends the `acknowledged`
    event. The next trunk start proceeds to bare `/milestone-plan`. `--abandon` from
    `MERGED_BEFORE_ACCEPTANCE`, with the item non-terminal in `<remote>/<trunk>`'s state,
    refuses, naming `--new-pr`, and writes nothing;
  - `--new-pr` on a `MERGED_BEFORE_ACCEPTANCE` record whose item `<remote>/<trunk>` records as
    `MILESTONE_COMPLETE` with no acceptance commit on `branch_point..H` refuses, naming
    `--abandon`, and writes no record and no event (TBR-R7-003). The same record after a human
    accepted on the branch outside the Controller (a trailered `A` on `branch_point..H`) and
    merged again: the gate names both exits, `--new-pr` is admitted, and the next preflight's
    PR-less close-out writes `MERGED`, then `CLOSED` (TBR-R8-003);
  - exit (c) with the plan approval commit already on `<remote>/<trunk>` (TBR-R8-004): the
    completed `BRANCH_BOUND` record makes no `gh pr create` call and launches the worker until
    the next branch commit, after which exactly one Draft PR is created. `--abandon` on it
    refuses on the remote-trunk precondition;
  - either disposition on a record that is not in a refusal state (other than `--abandon` on a
    branch-absent `BRANCH_PLANNED` record or a plan-stage `BRANCH_BOUND` record, CP6), or whose PR a fresh read shows reopened,
    refuses and writes nothing. Neither disposition runs any `git push`,
    `git switch`, ref deletion, or `gh pr` mutation (fake `gh` and git spies);
- close-out from trunk (TBR-R3-001): the human merges on the bare origin through the fake `gh`,
  then switches to `main` and pulls by hand. The next run, from record `PR_OPEN` and from record
  `READY`, writes `MERGED` then `CLOSED` without a switch, and continues to the trunk start. The
  same with a PR that is still open refuses, naming the branch. A PR closed without merge writes
  `PR_CLOSED_UNMERGED` and refuses. A local branch commit not in `H` gates. A crash in step 3
  after the switch and before `CLOSED` (record `MERGED`, `HEAD` on trunk) converges to
  `CLOSED`;
- close-out from trunk with the bound branch checked out in a second linked worktree refuses,
  naming that worktree, and writes no record; the `milestone-binding` dispositions refuse the
  same way (TBR-R4-002);
- a merge whose `H` records `MILESTONE_COMPLETE` without the `Workflow-Work-Item` trailer (phase
  set by hand, no `/accept-milestone` commit) writes `MERGED_BEFORE_ACCEPTANCE`, from the branch
  and from trunk, naming the untrailered commit, and never `CLOSED` (TBR-R4-004). With `H`
  absent locally and the remote branch deleted, `H` is fetched through `refs/pull/<n>/head`;
- a commit after the acceptance commit gates readiness with `post_acceptance_commits`, and a
  tip whose `MILESTONE_COMPLETE` commit lacks the `Workflow-Work-Item` trailer refuses;
- with a remediation child accepted on the branch before the parent (trailer
  `Workflow-Work-Item: <child-id>`), the acceptance commit is the parent's own later commit,
  readiness keys on it, and no second PR is created;
- a human merge from `PR_OPEN` after `MILESTONE_COMPLETE` converges through close-out to
  `CLOSED`, and one before it writes `MERGED_BEFORE_ACCEPTANCE` and gates every later step with
  `merged_before_acceptance` until an exit;
- a simulated squash merge (a single new commit on the bare origin's `main`, `mergedAt` set, `H`
  not an ancestor) writes `MERGED_REWRITTEN` with the `merge_method_rewrote_history` gate and no
  automatic switch;
- a human-set `isDraft == false` at `PR_OPEN` is tolerated by the per-preflight re-verification;
- readiness success calls `gh pr ready` exactly once, and a rerun is a no-op;
- at `READY`, repeated runs return the merge gate and never call a merge;
- close-out after a fake merge (the merge commit fast-forwarded onto the bare origin's `main`)
  switches to `main` and fast-forwards;
- a dirty tree at close-out gates.

### CP8 -- lifecycle wiring and observation

Files:
- `controller/job.py` (step 1b, post-step verification, the gate record);
- `controller/worker.py` and `controller/routing.py` (the disallowed-tool union when active);
- `controller/cli.py` (the inspect/explain/status blocks);
- `controller/cli.py` also gains the `milestone-binding` subcommand (TBR-R4-001);
- tests: `tests/test_cli.py`, `tests/test_lifecycle_orchestration.py`, `tests/test_worker.py`,
  `tests/test_observe.py`, `tests/test_plan_document_consistency.py` (`_COMMAND_NAMES` and the
  `extract_invocation_lines` docstring), and the new `tests/test_trunk_preflight.py`.

Tests:
- with no policy:
  - exactly the two probe argv (`rev-parse --path-format=absolute --git-common-dir`,
    `ls-tree -z HEAD -- .workflow-controller/policy.json`) and no other subprocess;
  - a policy committed at `HEAD` but deleted in the worktree is still active;
  - byte-identical job records, worker argv and `inspect`/`explain` JSON against goldens
    captured *before* CP8. The JSON has no `repository_policy`, `milestone_branch` or
    `repository_preflight` key at all, not even a `null` one;
  - on an unborn `HEAD` (a Workflow-installed repository with no commits): exactly the four
    probe argv, classified as absent, and the same 1.1.1 behaviour;
- with a policy:
  - a step on the wrong branch refuses;
  - on a bound branch, an explicit `--work-item` naming an unrelated top-level work item
    refuses, naming both ids, while the bound item and its remediation child are admitted
    (TBR-R3-004);
  - a worker that switches branch or rewrites history fails its job;
  - disallowed tools appear in the argv;
  - `inspect --json` carries the two new objects, and `explain --json` carries
    `repository_preflight`;
  - `explain` names the pending preflight action without fetching (a git spy sees no `fetch`
    and no `gh`);
  - `follow` output is unchanged;
- `milestone-binding` (TBR-R4-001):
  - the parser requires `--work-item` and exactly one of `--new-pr`/`--abandon`, and exits 2
    otherwise;
  - end to end through the CLI on a policy-enabled fixture: `--abandon` on a
    `PR_CLOSED_UNMERGED` record exits 0, and the next `step` from trunk launches bare
    `/milestone-plan`; `--new-pr` on a `MERGED_BEFORE_ACCEPTANCE` record exits 0, the next
    `step` on the branch launches the worker without a `gh pr create` call, and the step after
    the next branch commit creates a new Draft PR;
  - end to end, the PR-less close-out (TBR-R6-001): an accepted milestone whose PR is closed
    unmerged, then `--new-pr` exits 0, and the branch is fast-forwarded into `main` by hand on
    the bare origin. The next `step` on the branch exits as a bare `/milestone-plan` launch on
    `main`, with the record `CLOSED` and no `gh pr create` call in the fake `gh` log; the same
    from trunk after a hand `git switch main && git pull`;
  - end to end, a plan discarded after a completed bind (TBR-R8-001): the next `step` on the
    branch exits 10 with the `bound_item_missing` gate and launches no worker;
    `milestone-binding --abandon` exits 0; after `git switch main`, the next `step` launches bare
    `/milestone-plan` on `main`;
  - a refused disposition exits 20 and changes no record;
  - on a repository with no binding record it exits 20, and writes no binding record, no
    `events.jsonl` line and no job record; the runtime root contains exactly what the ordinary
    dispatch of a refused `step` leaves (TBR-R5-003);
  - `cli.ALL_COMMANDS` equals the subparser choices, including `milestone-binding`;
  - it creates no job record and launches no worker in any case.

### CP9 -- end-to-end disposable scenarios

Files: `tests/test_trunk_orchestration_e2e.py` (new; fake claude plus fake gh plus bare origin
plus the real Workflow 2.5.1 scripts via `build_workflow_line_fixture`).

Scenarios:
1. Policy-enabled lifecycle:
   - trunk start, then the scripted `/milestone-plan`;
   - bind;
   - the simulated plan-approval commit;
   - the Draft PR;
   - implementation steps with sync pushes;
   - `MILESTONE_COMPLETE`;
   - ready;
   - the merge gate;
   - the simulated human merge on the bare origin;
   - close-out;
   - the next trunk start.
2. The same lifecycle killed (SIGKILL of the Controller process) at each persist boundary and
   resumed. Assertions: one branch, one PR, no force, no merge call.
3. Trunk advanced mid-milestone: a readiness gate, and no integration performed. Then the
   human merges from `PR_OPEN` on GitHub (fake `gh`), and the Controller converges to `CLOSED`.
4. The identical scripted lifecycle with no policy: identical records to a pre-milestone run.
5. The release classification walked over a disposable trunk history that reproduces
   `v1.1.0`/`v1.1.1`, then a bump to 1.2.0, then an interrupted 1.2.0 publish after the tag
   push, resumed by the next merge.

### CP10 -- documentation and full verification under Workflow 2.5.1

CP10 is this milestone's terminal checkpoint. Acceptance follows it directly.

Files:
- `README.md` ("Milestone branches and pull requests" (new), "Continuous integration",
  "Releasing" rewritten, and "Controller-owned runtime state" gains `repositories/`);
- `docs/adr/0003-trunk-branch-pr-release-orchestration.md` (new);
- `docs/ROADMAP.md` (a new section for this milestone, marked in progress, and an entry for the
  follow-up Controller / Workflow 2.6.x integration milestone, listing the scope named under
  "Follow-up milestone boundary");
- the first-automatic-release runbook, as a README subsection. It includes the one-time
  repository settings: immutable releases on, and "Allow squash merging"/"Allow rebase merging"
  off. It also covers `BASELINE_UNRELEASED` and its two resolutions, and states that
  acknowledging a tag settles only that tag;
- the refusal-state exits (TBR-R4-001): reopening the PR, `milestone-binding --new-pr`, and
  `milestone-binding --abandon` with its precondition. It also says that re-planning an
  abandoned id first needs the operator to delete the old local and remote branches, because
  the Controller never deletes a branch. It also says that a PR whose head branch was deleted
  on GitHub cannot be reopened until the branch is restored there (round-5 usability note).
  It says the three exits are exclusive (after `--new-pr`, do not reopen the old PR; a
  reopened superseded PR must be closed again, TBR-R6-002), and that a branch already merged
  into `main` by hand, without a PR, is finished by `--new-pr`, after which the PR-less
  close-out records it `CLOSED` (TBR-R6-001, round-6 usability note). It also gives the exits
  of a `BRANCH_PLANNED` record left by a crash during the bind: switch to a branch the bind
  created, remove one it did not, or create the branch and switch to it when none exists, or
  `--abandon` when the plan was discarded (TBR-R7-001). It gives the exits of the
  `bound_item_missing` gate: restore the plan files, or, when nothing was committed on the branch,
  `--abandon` and then delete the leftover local `milestone/<id>` before planning the same id
  again (TBR-R8-001). It also says that creating the branch after trunk was rewound below
  `base_commit` opens a Draft PR in the plan-stage window, carrying the commits the rewind removed
  from trunk (TBR-R8-004, architecture challenge 1);
- a warning against GitHub's "Update branch" button while a milestone is in flight: it pushes a
  merge commit to the milestone branch, which the next preflight refuses. The
  `integration_required` procedure is the supported path (round-4 usability note);
- the README says plainly that under Workflow 2.5.1 the `integration_required` gate, followed
  by the manual merge procedure, is the **normal** path whenever anything lands on `main` during
  a milestone (including this repository's own post-acceptance release commits), until the
  follow-up integration milestone binds integration to the released Workflow 2.6.x.

Every new `workflow-controller ...` span must parse (`test_plan_document_consistency`).

Verification:
- the full suite;
- the conformance suites;
- `CONTROLLER_REQUIRE_PACKAGING_TESTS=1` packaged tests;
- `tools/ci_workflows.py --check`;
- a local `tools/release.py classify` of this repository's `HEAD` (expect `NO_CHANGE`).

The outputs are recorded in the checkpoint commit message.

## Follow-up milestone boundary: Controller / Workflow 2.6.x integration

This is **not a checkpoint of this milestone** (manual external plan review round 1, `I1`). The
user decided that the Workflow Manager 2.6 hardening milestone and this milestone run and finish
independently in parallel, and that only after both are complete does a separate follow-up
milestone integrate the Controller with the released Workflow 2.6.x. This milestone therefore
neither waits for nor depends on any Workflow 2.6 release, and requirement R8 of earlier
revisions (an explicit 2.6.x dependency checkpoint) is moved out of this milestone's mapping to
that follow-up rather than left falsely covered here.

The follow-up milestone's scope is planned from the actually released 2.6.x implementation, not
guessed now. It is expected to cover:
- upgrading this repository from Workflow 2.5.1 to the released 2.6.x through Workflow Manager
  (never by hand), between milestones;
- measuring the released contract (phases, state fields, commands) and answering E1-E5 from it;
- replacing the Controller's duplicated feedback-path resolver
  (`controller/evidence.py:170`, `resolve_feedback_dir`) with Workflow's authoritative one;
- re-anchoring the Controller's expected-outcome and state-writer declarations to the released
  2.6 commands;
- admitting the release in `managed_repo.VALIDATED_WORKFLOW_RELEASES` and updating the version
  fixtures, re-running the inventory and golden decision suites against every admitted release;
- migration coverage from 2.5.1 to 2.6.x, including an in-flight bound milestone;
- any branch/base/worktree binding that genuinely belongs to the released Workflow contract,
  including wiring `gitrepo.merge_trunk` to a released base-moving transition if E2-E4 provide
  one, or naming the narrow Workflow follow-up if they do not.

Until that milestone lands, the behaviour this milestone ships is the contract: trunk drift is
detected read-only, readiness fails closed with `integration_required`, and the documented manual
merge procedure is the supported path (CP7, CP10).

## Decisions for the reviewer and the user

This repository has no `docs/TECHNICAL_DECISIONS.md`, so there are no "Open decision" rows to
check against. The choices below are made in this plan and flagged rather than silently
finalised:

1. **Base commit `00c3b71`** rather than the previous acceptance commit `82fa6a8` (header).
2. **Branch binding happens after `/milestone-plan`, in the uncommitted plan-stage window**
   (Workflow 2.5.1 derives the id itself, fact 2). The alternative, an operator-supplied id with
   the branch created before planning, needs `/milestone-plan` to accept an unknown id. That is a
   Workflow change, recorded under E1 rather than worked around.
3. **Policy location and format:** `.workflow-controller/policy.json`, read from committed
   trees, with a per-binding snapshot.
4. **Desired-state release trigger** (compare with the last release tag in history, not with
   the previous push) makes failed or cancelled releases self-retrying, including after the tag
   push, through `RESUME` at the tag's own commit. The literal alternative,
   "diff against `github.event.before`", can permanently skip a version.
5. **Worker tool restrictions only when a binding is active** (I1). Applying them to every
   repository would change 1.1.1 behaviour for unconfigured targets.
6. **Network failure during a preflight refuses** (I9), even for plan-stage steps.
   Availability is traded for never acting on a stale view of the remote.
7. **Automatic close-out** switches to trunk and fast-forwards after a verified merge, with a
   clean tree only.
8. **`validate.yml` keeps major-tag actions** (ADR 0002). Only jobs that build release
   artifacts or hold a write token are SHA-pinned.
9. **No version bump inside this milestone.** 1.2.0 is the first automatic release, from a
   post-acceptance commit.
10. **Workflow 2.6.x upgrade timing** belongs to the follow-up integration milestone, which
    upgrades between milestones, never mid-flight in this one.
11. **CP11 is split out (TBR-R1-014, resolved by the user's decision; manual external plan
    review round 1, `I1`).** Revisions 1-9 kept a CP11 Workflow 2.6.x integration checkpoint and
    left the keep-or-split choice open. The user had already decided that this milestone and the
    Workflow Manager 2.6 milestone finish independently, with integration in a separate
    follow-up milestone afterwards. Revision 10 applies that decision: CP11 and its requirement
    (R8) are removed, CP10 is the terminal checkpoint, and this milestone is accepted under
    Workflow 2.5.1 with the fail-closed `integration_required` gate as its documented behaviour.
    The 1.2.0 release and the first dogfooded milestone therefore wait on no external date. No
    other checkpoint is reopened by the split.
12. **Legacy orphan tags are acknowledged explicitly** (`release.abandoned_tags: ["v1.1.0"]`),
    never inferred from "tag without release", and never skipped over by a later bump. An
    unacknowledged tag without a published release is resumable while its version is still
    current (`RESUME`), and blocks every later version (`BASELINE_UNRELEASED`), whether reached
    by a bump or by a hand-pushed tag, until it is either resumed or acknowledged. A release
    published by hand outside the transaction is outside this guarantee.
13. **Close-out requires the accepted head on trunk.** Squash and rebase merges are detected
    and recorded (`MERGED_REWRITTEN`), not accepted as a clean `MERGED`. The runbook disables them
    in repository settings.
14. **Refusal states have operator exits, and abandoning is narrow** (TBR-R4-001). A closed PR
    returns to `PR_OPEN` automatically when reopened. `milestone-binding --new-pr` continues the
    milestone on the same branch with a new Draft PR, and `--abandon` retires the binding. The
    Controller refuses `--abandon` while the work item's non-terminal state is on trunk, because
    Workflow 2.5.1 has no transition that retires a work item. After a merge before acceptance,
    continuing is therefore the only in-scope exit. The alternative, deleting the record file by
    hand, was rejected: it leaves the leftover branch adoptable by rule 2 and has no audit
    event. An abandoned binding's PR numbers stay excluded from PR discovery for every later
    binding of the same id, read from the renamed records (TBR-R5-002). An excluded PR that a
    human reopens is refused by name, never adopted (TBR-R6-002). A `BRANCH_PLANNED` record
    whose bind created no branch can be abandoned too, but only once the plan itself is gone
    from trunk; otherwise its exit is to create the branch and switch to it (TBR-R7-001). A
    `BRANCH_BOUND` record whose plan was discarded before anything was committed or pushed on
    the branch can be abandoned in the same way, and until then rule 1 gates
    `bound_item_missing` instead of planning on the bound branch (TBR-R8-001).
15. **A milestone merged into trunk without a PR is closed out, not abandoned** (TBR-R6-001).
    A `BRANCH_BOUND` or 0-match `PR_PLANNED` record whose tip is on trunk and whose item is
    `MILESTONE_COMPLETE` there takes the PR-less close-out to `CLOSED`, with `pr` left `null`.
    The alternative, a new refusal state exited through `--abandon`, was rejected: it would
    record as abandoned a milestone that completed. Every state's outcome, from the branch and
    from trunk, is enumerated in the outcome matrix under "Durable record", and a CP7 test pins
    it.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-trunk-branch-pr-release-orchestration-artifacts.json`
starts from `generate_artifacts_declarations` and is fitted to this plan's footprint:

- **Plan stage:** protected = this plan, its registry and its mapping. It inherits the
  exclusions. `.workflow-controller/` is added as an excluded prefix: it is implementation
  content, not plan design.
- **Implementation stage:** protected prefixes `controller/`, `tests/`, `tools/`, `docs/adr/`,
  `.workflow-controller/`. Protected paths:
  - `pyproject.toml`, `setup.py`, `README.md`;
  - `.github/workflows/validate.yml`, `.github/workflows/ci.yml`,
    `.github/workflows/main.yml`, and `.github/workflows/release.yml` (a deletion is a reviewed
    change);
  - the artifacts file itself.

  `docs/ROADMAP.md` and `docs/ACTIVE_MILESTONE.md` stay excluded (narrative), as in the previous
  milestone. This milestone makes no Workflow Manager upgrade, so `scripts/`,
  `.claude/commands/`, `.workflow-manager/` and `workflow-conformance.yml` are not edited and keep
  their inherited classification.

## Verification

Per checkpoint: the full Controller suite, `--check`, and the checkpoint's own new modules
green. At CP10: all of the above plus the seven conformance suites and the packaged-runtime
suite under `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`. Nothing in verification needs network,
`claude` or real GitHub: `gh` is always the fake, and origins are local bare repositories.

## Migration / data-integrity notes

- There is no migration of existing runtime records. `repositories/` is additive.
- The version move is behaviour-preserving: the string stays `1.1.1`, and `--version`,
  `BUILD_INFO`, `METADATA` and job records carry the same value.
- `v1.1.0` (tag without release) and `v1.1.1` (released) are left as they are. `v1.1.0` is listed
  in the reference policy's `abandoned_tags`, so it is never resumed. Today's `main` classifies
  as `NO_CHANGE`, and a replay of a 1.1.0-era commit classifies as `ABANDONED_VERSION`. Neither
  tag is ever touched. A future tag left without a release is never skipped over by a later bump or a
  later hand-pushed tag: the later version fails as `BASELINE_UNRELEASED`, in the `RESUME` shape
  as well as the `RELEASE_DUE` shape, until the tag is resumed or acknowledged.
- A bound milestone's state lives on its branch, so from trunk the Controller reads its binding
  records, not trunk's `WORKFLOW_STATE.json`. A second milestone cannot start on trunk while one
  is bound and unfinished, which avoids the E5 `WORKFLOW_STATE.json` conflict by refusal. A
  merge closed out by hand on trunk converges to `CLOSED` rather than leaking a live record.
  A binding in a refusal state blocks the repository only until one of its exits runs (reopen,
  `milestone-binding --new-pr`, or `--abandon`). Records are never deleted: `--abandon` writes
  `ABANDONED`, and a later bind of the same id renames the old record aside.
- A squash or rebase merge would leave Workflow provenance on `main` unreachable or rewritten.
  The Controller detects this at close-out (`MERGED_REWRITTEN`), and the runbook's repository
  settings prevent it.
- A repository with an active policy whose runtime root is lost (for example a new machine)
  re-derives its binding through the adopt row, mid-implementation or after acceptance (with the
  acceptance commit in `P..HEAD`). That needs `HEAD` on `milestone/<id>`,
  `P = merge-base(<remote>/<trunk>, HEAD)` with `base_commit` an ancestor of `P`, the plan
  approval commit (if any) off trunk, and the policy read at `P`. A remote-only branch still
  refuses, by design. Remediation children are never re-derived: they ride on their parent's
  adopted binding.
- A lost runtime root also loses the PR exclusions its records held (`superseded_prs` and the
  renamed abandoned records). If the adopted binding's PR discovery then finds two or more
  merged PRs for the branch, the Controller cannot tell which is its own and refuses, naming
  them (TBR-R6-003). The recovery is to restore `<runtime_root>/repositories/<repo_key>/` from a
  backup. Without a backup this is a stated limitation of this milestone, like the remote-only
  branch above: the refusal persists, and repairing the record by hand is outside the
  Controller's guarantees.
- A binding completed through the `BRANCH_PLANNED` catch-all's exit (c) after the plan approval
  commit already reached `<remote>/<trunk>` converges, but it carries two stated limitations
  (TBR-R8-004). First, its approval commit is on trunk, so the adopt row's "approved on trunk"
  refusal applies: a lost runtime root cannot re-derive this binding, and the same recovery as
  above applies (restore the runtime root from a backup). Second, `--abandon`'s remote-trunk
  precondition fails for the binding's whole life, because trunk carries the item's non-terminal
  state. So `--new-pr` is its only refusal-state exit, as after any merge before acceptance.
  Restricting (c) to an approval that is not yet on `<remote>/<trunk>` was the alternative. It was
  not taken, because the "approved on trunk" manual recovery it would route to has no
  in-scope exit for a `BRANCH_PLANNED` record either (`--abandon` fails the same precondition),
  whereas (c) at least converges to a milestone that proceeds.
- A plan discarded after a completed bind no longer strands the repository (TBR-R8-001). From
  the branch, rule 1 gates `bound_item_missing` before `decide`'s bare `/milestone-plan` can run
  on the bound branch. In the plan-stage window, `--abandon` retires the binding, and the next
  trunk start plans on trunk. The leftover local `milestone/<id>` at `T` must be deleted by the
  operator before the same id is planned again, as for any abandoned binding.

## Plan review decisions

### Round 1 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 1) -> revision 2

Every finding was validated against the repository before it was applied.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R1-001 | accepted | `82fa6a8` carries `Workflow-Work-Item:`, and `.claude/commands/accept-milestone.md:147` requires it. Commits `5832b16` and `00c3b71` show that post-acceptance commits happen. Added the acceptance commit `A` (the first-parent commit where `MILESTONE_COMPLETE` first appears, cross-checked by the trailer), readiness condition 3 (tip == `A`), the `post_acceptance_commits` gate, I7 reworded, and a CP7 test. The "admissible excluded-only post-acceptance commits" alternative was not taken, because the empty set is simpler and fail-closed. |
| TBR-R1-002 | accepted | Confirmed from the old row order: `NO_CHANGE` preceded any completeness check. `RESUME` now covers a tag at a strict ancestor and targets the tag's own commit. `NO_CHANGE` requires a published release. `abandoned_tags` in the policy acknowledges `v1.1.0` explicitly (`ABANDONED_VERSION`/`ABANDONED_TAG_INCONSISTENT`). The wording is corrected, and CP4/CP9 tests are added. Partially rejected: a `workflow_dispatch` input naming the commit or tag. Dispatch classifies the trunk tip, and `RESUME` already targets the tag's commit, so an input would add a second, user-typed source of the target for no gain. |
| TBR-R1-003 | accepted | Confirmed: `controller/target_state.py:394` reads the working tree, so no existing Git call can be reused. Chose the fixed two-call budget (`rev-parse --git-common-dir`, `ls-tree HEAD -- <path>`). I1, lifecycle wiring and the CP8 tests are updated. The working-tree short-circuit was rejected, because a committed-but-deleted policy would silently deactivate. |
| TBR-R1-004 | accepted | Confirmed: `discover_approval_commits` (`scripts/workflow_state.py:1756`) is first-parent and trailer-based, so a squash or rebase drops or rewrites that evidence on `main`. Close-out now requires the final `headRefOid` `H` to be an ancestor of trunk; otherwise `MERGED_REWRITTEN` plus the `merge_method_rewrote_history` gate. There is no policy merge-method field (only one admissible value). The runbook disables squash/rebase, and a CP7 test is added. |
| TBR-R1-005 | accepted | Confirmed: `identity.py:625,635` writes `version.__version__`, `_extract_package` (`identity.py:461`) copies only `controller/`, and `pin()` (`identity.py:666`) never reads the pin's `version`. The pin's version is now defined per kind (the snapshot's `pyproject.toml`, or the snapshot's `controller/BUILD_INFO.json`), with no fallback, plus CP1 tests. |
| TBR-R1-006 | accepted | `PR_OPEN -> MERGED`, `MERGED_BEFORE_ACCEPTANCE`, a human-set non-draft tolerated, and close-out entry states `PR_OPEN`/`READY` are all stated. Tests are added to CP7 and CP9. |
| TBR-R1-007 | accepted | The circular condition is replaced by `P = merge-base(<remote>/<trunk>, HEAD)`. The full adopt precondition set is stated, the policy is read at `P`, and a catch-all refusal row makes the table total. CP6 tests are added. |
| TBR-R1-008 | accepted | The concurrency semantics are restated (one running plus one pending, and a newer pending run cancels the older one). The release commit can be later than the bump commit. |
| TBR-R1-009 | accepted | `release-plan` gets `GH_TOKEN` under read-only permissions, asserted in CP5. |
| TBR-R1-010 | accepted | The forge static scan is now argv-scoped (`"api"` following `"gh"`), with a CP3 test. |
| TBR-R1-011 | accepted | Took the first option: the three tag-first subcommands stay until CP5, which removes them together with `release.yml`. |
| TBR-R1-012 | accepted | "No checks reported" is `checks_pending`, tested in CP7. |
| TBR-R1-013 | accepted | The admitted phases are enumerated, and `AMENDING_PLAN` is excluded (`KNOWN_PHASES` is a frozenset, `scripts/workflow_state.py:331`). Tested in CP6. |
| TBR-R1-014 | accepted as a user decision | Added as decision 11, with a recommendation to split. The registry is unchanged until the user chooses. |

The architecture notes from the review are also applied: the `package_version` name/`RECORD`
probe note (Version authority), and the one-branch-per-worktree reliance (Durable record).

Registry and mapping: no checkpoint was added, removed or renamed. This round changed tests and
behaviour inside existing checkpoints, so both are regenerated at plan revision 2 with the same
checkpoints and requirements.

### Round 2 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 2) -> revision 3

Every finding was validated against the repository before it was applied. All eight are accepted.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R2-001 | accepted | Confirmed: `create_remediation_child_work_item` (`scripts/workflow_state.py:10216`) creates `<parent>-remediation-<n>` in `PLANNING`, with `base_commit` at the parent's implementation head and `active_work_item_id` untouched. `controller/job.py:284-322` already tracks `child_work_item_ids`, and `incomplete_children` (`scripts/workflow_state.py:7811`) blocks the parent's acceptance. Added "Remediation children never get their own binding" and a single "Work-item resolution" order (binding for `HEAD`'s branch, then the adopt candidate from the branch name, then the top-level bind candidate on trunk), which also answers the architecture note. The bind trigger, adopt table and adopt preconditions exclude children. The acceptance-commit definition now says why a child's own `MILESTONE_COMPLETE` commit is never the parent's `A`. CP6 and CP7 tests are added. |
| TBR-R2-002 | accepted | Confirmed from the revision-2 table: with no `tag` for `V`, only `version(B)` was compared, never `B`'s release status. Added the "settled" baseline (published, or in `abandoned_tags`), the `BASELINE_UNRELEASED` row before `INVALID_TRANSITION`/`RELEASE_DUE`, the extra `view_release` for `B`, the two resolutions (acknowledge, or resume by a commit restoring the old version, which is `RESUME` at the tag's commit), and the induction argument. Decision 12 and the migration notes are corrected. CP4 tests cover the unreleased, draft, acknowledged and resumed variants, and the CP10 runbook covers the resolutions. The previously rejected `workflow_dispatch` input stays rejected: the resume commit reaches the tag's commit without a second, user-typed target source. |
| TBR-R2-003 | accepted, first option | Omit, never `null`. With no policy at `HEAD` and no binding, `inspect`/`explain` JSON gains no key, so I1's byte-identical promise and the CP8 golden test hold as written. I1, "Observation (I10)" and the CP8 tests now state the same rule, and `explain`'s new JSON key (`repository_preflight`) is named and covered by it. |
| TBR-R2-004 | accepted | Measured: `git ls-tree -z HEAD -- x` in a fresh `git init` exits 128, `rev-parse --verify -q HEAD` exits 1 and `symbolic-ref -q HEAD` exits 0. `job._current_head` (`controller/job.py:206`) returns `None` there, so 1.1.1 tolerates it. The probe now classifies exactly that combination as absent, with two extra read-only calls on the unborn path only. The budget is two calls, or four on an unborn `HEAD`, with a CP8 test. |
| TBR-R2-005 | accepted | Confirmed: `tools/release.py:324` reads `version_module.__version__` for every subcommand. CP1 switches `main()` to `version.source_version(<repository root>)`, and CP4 moves `version` and the new subcommands to the policy's version source. Both are stated under "Version authority", with a CP1 test. |
| TBR-R2-006 | accepted | Asset-set consistency now runs `verify` for the target commit (the tag's own commit for `RESUME` at a strict ancestor), matching the transaction section. A CP4 test is added. |
| TBR-R2-007 | accepted | `gh pr checks` uses exit 8 for pending and exit 1 for a failing check or "no checks reported". The forge contract classifies by exit code and output together, and anything else is undecidable. `fake_gh` reproduces the codes, with a CP3 test. |
| TBR-R2-008 | accepted, admitting the case | Adopt now admits a `MILESTONE_COMPLETE` work item whose acceptance commit is on the first-parent path `P..HEAD`, and continues to PR discovery and readiness. The migration notes are updated, with a CP6 test. A manual-merge-only statement was the alternative, but the resolution order already identifies the item from the branch name, so admitting it costs one precondition. |

The usability note is applied too: CP10's README says that `integration_required` followed by
the manual merge is the normal path under Workflow 2.5.1.

Registry and mapping: no checkpoint was added, removed or renamed, and no requirement's coverage
changed. Both are regenerated at plan revision 3 with the same 11 checkpoints and 18
requirements, and the embedded table is re-rendered.

### Round 3 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 3) -> revision 4

Every finding was validated against the repository before it was applied. All four are accepted.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R3-001 | accepted | Confirmed: `target_state.read` (`controller/target_state.py:394`) reads `<root>/docs/ai-workflow/WORKFLOW_STATE.json` from the working tree, and the bind carries the item's entry to the branch uncommitted, so from the plan approval commit onwards trunk's state file has no entry for a bound item. Resolution rule 3 now reads the binding records for the `repo_key` first: any record in a non-terminal state (including `MERGED`) or a refusal state blocks and names its branch, unless the adopt table's `BRANCH_PLANNED` rows or the new "Close-out from trunk" reconciliation applies. That reconciliation converges a merge closed out by hand (and a crash between step 3's switch and `CLOSED`) to `MERGED`, then `CLOSED`, without a switch. It reuses merged-PR handling steps 1 and 2 unchanged, because they read only `H:` and `<remote>/<trunk>`. The trunk-start preflight's entry condition and the migration notes are restated. Tests: the refusal cases are in CP6 as requested. The manual close-out convergence test is in CP7, not CP6, because the merged-PR handling it exercises is CP7 code and CP6 precedes it. |
| TBR-R3-002 | accepted | Confirmed from the revision-3 table: `RESUME` matched before `BASELINE_UNRELEASED`, which required "no `tag`". `B` is now the highest ancestor tag with a version strictly below `V`, and `M` (the highest ancestor tag at all) serves `INVALID_TRANSITION` alone. `BASELINE_UNRELEASED` moved above `RESUME`, which is safe because `RESUME` is disjoint from `NO_CHANGE` and `COLLISION_RELEASE_WITHOUT_TAG`. The resume resolution still works (the revert-to-1.2.0 commit's baseline is `v1.1.1`, published). The induction now covers both publishing rows, and states that a release published by hand outside the transaction is outside the guarantee. Decision 12 and the migration notes are corrected, and CP4 tests cover the hand-pushed-tag scenario, its acknowledged variant, and the resume commit's baseline. |
| TBR-R3-003 | accepted, own gate | Confirmed: `gh pr checks --help` (`gh` 2.101.0) documents the buckets `pass`, `fail`, `pending`, `skipping` and `cancel`. `bucket` is now a closed set in the forge contract, and any other value is `ForgeUndecidableError`. `cancel` gets its own gate, `checks_cancelled`, because the remedy (re-run) differs from a failure. The precedence is failing, then pending, then cancelled. `fake_gh` emits `cancel`, with CP3 and CP7 tests. |
| TBR-R3-004 | accepted | Rule 1 now gates a terminal binding with `HEAD` on its branch as `switch_to_trunk`. The post-acceptance override never re-enters PR discovery or readiness from it. A selection naming a work item that is neither the bound item nor one of its children refuses, naming both. Tests are in CP6 (terminal binding) and CP8 (foreign `--work-item`, which exercises the preflight's use of `decide`'s selection). |

The architecture note on the unborn-`HEAD` probe is applied too: a paragraph under the probe now
says why it cannot be more lenient than 1.1.1 on a corrupt repository, and how an orphan branch
is classified. The `gh pr checks` note (classify by exit code and output together) is kept as it
is.

Registry and mapping: no checkpoint was added, removed or renamed, and no requirement's coverage
changed. Both are regenerated at plan revision 4 with the same 11 checkpoints and 18
requirements, and the embedded table is re-rendered.

### Round 4 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 4) -> revision 5

Every finding was validated against the repository before it was applied. All four findings and
the usability note are accepted.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R4-001 | accepted | Confirmed from revision 4's own text: rule 3.1 blocked on `PR_CLOSED_UNMERGED`/`MERGED_BEFORE_ACCEPTANCE` with no transition out, `ABANDONED` was "out of scope beyond the state name", and the no-policy probe returns `Proceed(None)` only with no binding record, so removing the policy did not help. Also confirmed: Workflow 2.5.1 has no work-item abandonment (`TERMINAL_PHASES = frozenset({"MILESTONE_COMPLETE"})`, `scripts/workflow_state.py:321`). One classification (non-terminal, refusal, terminal) is now stated once under "Durable record" and used by rule 1 and rule 3.1. The "while the work item is non-terminal" condition is dropped. Exits: reopen (automatic, branch-side, I6-verified), and the new `milestone-binding` subcommand (`controller/cli.py`'s `build_parser` has no such subcommand today) with `--new-pr` (a new Draft PR on the same branch, with the old number in `superseded_prs`) and `--abandon` (writes `ABANDONED`, refused while the item's non-terminal state is on `<remote>/<trunk>`). Neither touches refs or PRs, and both append an event. An `ABANDONED` record keeps gating its leftover branch through rule 1, so the branch is never adopted. Re-planning the same id renames the old record aside after the operator removes the old branches. Deleting the record file by hand was rejected (decision 14). Tests are in CP6 (classification, the leftover branch, re-bind of an abandoned id), CP7 (each exit) and CP8 (the CLI end to end, including the trunk start after `--abandon`). CP10's runbook documents the exits. |
| TBR-R4-002 | accepted, first option | The premise was indeed false for trunk-side writes. `gitrepo.worktree_branches()` (`git worktree list --porcelain`, read-only) is added in CP3. Every record write made without `HEAD` on the bound branch (close-out from trunk, `milestone-binding`) first requires the bound branch not to be checked out in another worktree, and refuses naming it otherwise. "Durable record" states the two forms of the rule. CP3 and CP7 tests are added. |
| TBR-R4-003 | accepted, first option | Every matching ancestor tag below `V` is now checked for settledness (the baseline set and the unsettled set), not only the highest. That removes the induction. Acknowledging a tag settles only that tag. The cost is one `view_release` per non-acknowledged lower ancestor tag, two for this repository today. Per-tag reads were chosen over `gh release list`, whose `--limit` (default 30) would add a truncation case. The table's `BASELINE_UNRELEASED`, `RESUME` and `RELEASE_DUE` rows, the examples, decision 12 and the CP4 tests are updated. |
| TBR-R4-004 | accepted | Merged-PR handling step 1 now looks for the acceptance commit `A` on the first-parent path `branch_point..H`, with readiness's own definition (trailer required), instead of the phase at `H`. No such `A` is `MERGED_BEFORE_ACCEPTANCE`, which now has TBR-R4-001's `--new-pr` exit. Close-out from trunk reuses the step unchanged, so `CLOSED` is written only when `A` is on trunk. If `H` is missing locally (the remote branch was deleted after the merge), it is fetched through `refs/pull/<n>/head`, and a still-missing `H` refuses (I9). A CP7 test is added. |
| Usability note | accepted | CP10's runbook warns against GitHub's "Update branch" button while a milestone is in flight. The preflight's refusal for a remote branch that is not an ancestor names it as the likely cause. |

Registry and mapping: no checkpoint was added or removed, and no requirement's coverage changed.
The names of CP7 and CP8 now include the refusal-state exits and the `milestone-binding`
subcommand, the one new CLI surface. Both files are regenerated at plan revision 5 with the same
11 checkpoints and 18 requirements, and the embedded table is re-rendered.

### Round 5 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 5) -> revision 6

Every finding was validated against the repository before it was applied. All four findings and
the usability note are accepted.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R5-001 | accepted | Confirmed from revision 5's own text: PR creation ran whenever the branch had a commit beyond `branch_point`, which is always true after `--new-pr` from `MERGED_BEFORE_ACCEPTANCE`, while GitHub refuses to create a PR whose head has no commit beyond its base ("No commits between ..."). The record then looped in `PR_PLANNED`, which is not a refusal state, so `milestone-binding` could not help either. The creation condition is now "the local tip is not an ancestor of, or equal to, the freshly fetched `<remote>/<trunk>`", which equals the old one for every first PR (a plan approval commit on trunk is refused at bind). While it is false the preflight skips creation, the record stays `BRANCH_BOUND`, and the worker step runs. PR discovery's 0-match row re-checks it, returning `PR_PLANNED` to `BRANCH_BOUND` if trunk was fast-forwarded meanwhile. `fake_gh pr create` now rejects a head with no commit beyond the base, and CP7/CP8 tests cover no create at `H`, the worker still running, and exactly one new Draft PR after the next branch commit, gated on `integration_required`. |
| TBR-R5-002 | accepted, first option | Confirmed: `list_prs` is `gh pr list --head <branch> --state all`, and `--head` filters by branch name, so the abandoned binding's closed PR matches the re-created `milestone/<id>`. PR discovery now excludes the union of the live record's `superseded_prs` and the `pr.number`/`superseded_prs` of every `<work_item_id>.abandoned-<n>.json` for the `repo_key`. The renamed record exists before the new record's O_EXCL create, so the exclusion survives the rename/create crash window with nothing copied. "Resolution never reads a renamed record" is narrowed to work-item resolution, with PR discovery as the renamed records' only reader. The events log is not renamed: it spans bindings, and the record and every event carry `binding_generation`. Separately, discovery's closed-unmerged row no longer refuses from `PR_PLANNED` with "reopen" as its only remedy: with cross-binding PRs excluded, a closed-unmerged match is this binding's own PR, so it writes `PR_CLOSED_UNMERGED` and the refusal-state exits apply. The discovery rows are now ordered and total. `fake_gh pr list --head` filters by name over closed PRs too. CP6 and CP7 tests cover the re-bind through to the first PR, with a kill between the rename and the create. |
| TBR-R5-003 | accepted, first option | Confirmed: `cli._dispatch` (`controller/cli.py:1013-1062`) runs `runtime.ensure_runtime_root`, `identity.materialise` from an unpinned source, and `_write_identity_record` before any subcommand body. `milestone-binding` keeps the ordinary dispatch (it is a mutating command and runs pinned). The claim and the CP8 test are narrowed to no binding record, no `events.jsonl` line and no job record, with the runtime root matching a refused `step`'s. Also confirmed and added: `cli.ALL_COMMANDS` (`controller/cli.py:39`) is pinned by `tests/test_cli.py:121`, and `extract_invocation_lines`'s docstring (`tests/test_plan_document_consistency.py:395`) says "seven". |
| TBR-R5-004 | accepted | The partial relation under "Durable record" is replaced by one complete transition table, including the entries into the refusal and terminal states and `PR_PLANNED` into the merged-PR handling. The merged-PR handling's entry sentence names `PR_PLANNED` too. Every record write is checked against the table, and a CP6 test pins it over all (from, to) pairs. |
| Usability note | accepted | The `pr_closed_unmerged` gate and the CP10 runbook say that a PR whose head branch was deleted on GitHub cannot be reopened until the branch is restored there, and that `--new-pr` or `--abandon` is otherwise the exit. |

Registry and mapping: no checkpoint was added, removed or renamed, and no requirement's coverage
changed. Both are regenerated at plan revision 6 with the same 11 checkpoints and 18
requirements, and the embedded table is re-rendered.

### Round 6 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 6) -> revision 7

Every finding was validated against the repository and the plan text before it was applied.
All four findings, the recommended outcome matrix and the usability note are accepted.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R6-001 | accepted, first option (PR-less close-out) | Confirmed from revision 6's own text: the transition table's only `BRANCH_BOUND` edge was to `PR_PLANNED`, the creation condition stays false forever once the tip is on trunk and the item is `MILESTONE_COMPLETE` (no worker step follows acceptance), readiness requires `PR_OPEN`, rule 3.1's reconciliation list omitted `BRANCH_BOUND`, and `milestone-binding` refuses non-refusal states. A new **PR-less close-out** applies to a `BRANCH_BOUND` record, or a 0-match `PR_PLANNED` record, whose tip `H` is on the fetched `<remote>/<trunk>` with the item `MILESTONE_COMPLETE` at `H`. It runs merged-PR handling step 1 on `H` (no trailered `A` writes `MERGED_BEFORE_ACCEPTANCE` with `pr == null`), otherwise writes `MERGED` with `pr` null and the new `merged_head` = `H`, then close-out step 3 from either side. `MERGED` now always records `merged_head`, which step 3 reads, so it never depends on `pr`. New edges `BRANCH_BOUND`/`PR_PLANNED` -> `MERGED`/`MERGED_BEFORE_ACCEPTANCE`; rule 3.1 reconciles `BRANCH_BOUND` and `PR_PLANNED`, and a `PR_PLANNED` record from trunk runs discovery read-only with stated per-row outcomes. `milestone-binding` common check 3 is skipped for a null `pr`. When trunk already records the item `MILESTONE_COMPLETE` without an acceptance commit, `--new-pr` would loop back, so the `merged_before_acceptance` gate names `--abandon` alone. The rejected alternative (a refusal state exited by `--abandon`) is recorded as decision 15. CP7 and CP8 tests cover the branch side, the trunk side, the 0-match `PR_PLANNED` row, the untrailered case, and no `gh pr create` call. |
| Outcome matrix (recommended) | accepted | Added under "Durable record": one row per state, with `BRANCH_BOUND` split three ways, and branch/trunk columns whose every cell names a writer, a gate with an exit, or a refusal with an exit. Building it exposed three further cells with no stated exit, now fixed: `BRANCH_PLANNED` with the branch absent and trunk moved past `T` re-runs the bind at the new tip when the bind preconditions hold (previously a catch-all refusal with no exit); `BRANCH_PLANNED` with the branch at a descendant of `T` completes to `BRANCH_BOUND`; and close-out step 3's unmerged-commit gate names its exit (move the commits off the branch, reset it to `merged_head`), with the no-rewrite check not applied in `MERGED` so that exit is not refused. A trunk-side record whose branch exists neither locally nor on the remote refuses naming the last observed tip, and its exit is restoring the branch there. A CP7 test pins every cell from both sides and asserts one row per classified state. |
| TBR-R6-002 | accepted | Confirmed: GitHub rejects a second open PR for the same head and base (HTTP 422, "A pull request already exists"), and discovery excluded superseded and abandoned numbers entirely, so a reopened excluded PR drove a create that always failed. Discovery now also yields the excluded open PRs, and a new first row refuses, naming each as superseded or abandoned and saying to close it on GitHub; it is never adopted and no create is attempted. `fake_gh pr create` rejects a duplicate open head/base, and a CP7 test covers the reopened superseded PR. |
| TBR-R6-003 | accepted | Split the row. "2+ open" stays as a defensive refusal; its CP7 fixture seeds `fake_gh`'s state directly, since the real forge (and now `fake_gh pr create`) cannot produce it. "2+ merged" no longer claims closing resolves it: it arises only after a lost runtime root lost the exclusions, and the refusal names the lost-runtime-root recovery, added to "Migration / data-integrity notes" as a stated limitation when no backup exists. |
| TBR-R6-004 | accepted | `binding_generation` is now "counted immediately before the O_EXCL create, after any rename". |
| Usability note | accepted | The `pr_closed_unmerged` gate and the CP10 runbook say the exits are exclusive (do not reopen the old PR after `--new-pr`) and that a branch already merged into trunk by hand is finished with `--new-pr`, after which the PR-less close-out records `CLOSED`. |

Registry and mapping: no checkpoint was added, removed or renamed, and no requirement's coverage
changed. Both are regenerated at plan revision 7 with the same 11 checkpoints and 18
requirements, and the embedded table is re-rendered.

### Round 7 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 7) -> revision 8

Every finding was validated against the plan text before it was applied. All four findings and
the usability note are accepted. Architecture challenge 1's optional "or push trunk" wording is
not taken: that refusal already names the branch, and a push of trunk is the operator's call.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R7-001 | accepted; sub-case (c) by a create-and-switch exit plus a narrow `--abandon` | Confirmed from revision 7's text: the matrix's `BRANCH_PLANNED` trunk cell and the catch-all row named one exit, "remove or rename the branch the bind did not create". For a branch at a descendant of `T` with `HEAD` on trunk, that exit discards the bind's own branch and does not converge. For an absent branch with a failing bind precondition (plan approved on trunk after the crash, or trunk rewound below `base_commit`), no exit existed: common check 1 refused non-refusal states and `Proceed(None)` needs no record. The exit is now split by observation in the matrix and in the catch-all row: (a) a branch at `T` or a descendant: switch to it, and the complete-from-descendant row writes `BRANCH_BOUND`; (b) a branch not descending from `T`: remove or rename it; (c) no branch: create `milestone/<id>` at the current trunk tip if it descends from `T`, else at `T`, and switch to it, so the same completion row applies. This needs no new Controller write and touches no ref (the human creates the branch the crash prevented). For a plan discarded after the crash, `milestone-binding --abandon` is admitted on a branch-absent `BRANCH_PLANNED` record, with the existing remote-trunk precondition plus a local one (no non-terminal entry in the working tree's state), so a trunk start never re-finds the item with no record to complete. New edge `BRANCH_PLANNED -> ABANDONED`; common checks 1 and 3 updated. CP6 tests cover (a), (b), (c) with approval on trunk, (c) with a rewound trunk, and `--abandon` with and without the plan present. The CP10 runbook lists the exits. |
| TBR-R7-002 | accepted | Confirmed: the trunk cell said "any other row refuses, naming the branch (exit: switch to it)", but the prose keeps the excluded-open, 2+ open and 2+ merged refusals unchanged, and switching to the branch re-runs the same discovery. The cell now lists every discovery row with its own exit, including the 0-match row that does not qualify for the PR-less close-out. The close-out-from-trunk prose says the same. The CP7 matrix test has one trunk-side fixture per discovery row. |
| TBR-R7-003 | accepted | A new common check 5 refuses `--new-pr` on a `MERGED_BEFORE_ACCEPTANCE` record when `<remote>/<trunk>` records the item `MILESTONE_COMPLETE`, naming `--abandon`. It is evaluated exactly as the gate's `--abandon`-alone case, so the command accepts only dispositions the gate names. There is one new CP7 test. |
| TBR-R7-004 | accepted | The CP7 wording is now "every classified state has at least one row, and a split state's rows partition it", and the matrix introduction says the same. A paragraph after the matrix states that every trunk cell that needs `H` (the three `BRANCH_BOUND` cells and the `PR_PLANNED` 0-match row) refuses when the branch is gone, naming the last observed tip, and the CP7 matrix test covers it. |
| Usability note | accepted | The `merged_before_acceptance` gate now says why `--new-pr` does not apply in its `--abandon`-alone case. |

Registry and mapping: no checkpoint was added, removed or renamed, and no requirement's coverage
changed. Both are regenerated at plan revision 8 with the same 11 checkpoints and 18
requirements, and the embedded table is re-rendered.

### Round 8 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`, reviewed plan revision 8) -> revision 9

Every finding was validated against the repository and the plan text before it was applied. Both
important findings, all three optional findings and the usability note are accepted.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| TBR-R8-001 | accepted; a gate plus a plan-stage `--abandon` | Confirmed: `decision.decide_no_work_item` (`controller/decision.py:972`) unconditionally selects bare `/milestone-plan`. Revision 8's rule 1 refused only a selection that *names* an unrelated item, so a bound branch whose plan was discarded would have launched `/milestone-plan` there. From trunk, the `BRANCH_BOUND` record blocked with no exit, because common check 1 admitted only refusal states and `BRANCH_PLANNED`. Rule 1 now has a fourth case, gate `bound_item_missing`: a non-terminal binding whose bound item has no entry in the working tree's `WORKFLOW_STATE.json` gates before `decide`'s selection is used. A `BRANCH_PLANNED` record completes first, so its gate then offers the same exits. The gate names two exits: restore the plan files, or `milestone-binding --abandon`. `--abandon` is admitted for a `BRANCH_BOUND` record only in the plan-stage window: the local tip equals `branch_point`, no remote branch exists, `pr` is null, and there is no non-terminal entry in the working tree, in `HEAD`'s committed state or on `<remote>/<trunk>`. New edge `BRANCH_BOUND -> ABANDONED`, and common checks 1 and 3 are widened. From trunk, the `BRANCH_BOUND` refusal names `--abandon` when its precondition holds. The matrix introduction, decision 14, the migration notes and the CP10 runbook say the same. The CP6 tests cover the branch-side gate, the trunk-side refusal, `--abandon` from both sides converging to a trunk-side bare `/milestone-plan`, the restore exit, each refused `--abandon` precondition, and the `BRANCH_PLANNED`-on-branch case. CP8 has one end-to-end case through the CLI. |
| TBR-R8-002 | accepted; both rows now name exits | Confirmed: the table is first-match, and "record present, branch tip not a descendant of `branch_point`" preceded the catch-all with no exit. The table now states that only `BRANCH_PLANNED` records reach its record-present rows (rule 1 and rule 3.1 resolve every other state). That row is now `BRANCH_PLANNED`-specific and refuses with exit (b), and the catch-all's (b) points to it. The forge/common-dir row moved ahead of the completion rows, so a mismatched repository is never completed silently. It names its exit: restore the remote's URL, or `--abandon` when its precondition holds. It no longer compares the policy, because every binding is governed by its recorded snapshot and the re-bind row re-reads it at the new tip. The CP6 (b) fixture now asserts the row that matches first, plus the forge-mismatch and policy-difference cases. |
| TBR-R8-003 | accepted | Common check 5 and the `merged_before_acceptance` gate now both say "without an acceptance commit `A` on the first-parent path `branch_point..H`", with `H` read as the PR-less close-out reads it. That is exactly the search `--new-pr`'s follow-up close-out performs, so check 5 refuses only when `--new-pr` would loop. A new CP7 test covers an acceptance commit made on the branch outside the Controller and then merged again. |
| TBR-R8-004 | accepted; consequences stated rather than restricting (c) | The PR creation condition prose now names exit (c) with the approval already on `<remote>/<trunk>` as the one first-PR exception, and rule 3's premise names it as the one case where trunk's state shows a bound item (harmless, because rule 3 reads the records first). Restricting (c) was not taken. The "approved on trunk" recovery it would route to has no in-scope exit for a `BRANCH_PLANNED` record either, because `--abandon` fails the same remote-trunk precondition. Two limitations are stated in the migration notes: no adopt after a lost runtime root, and `--new-pr` as the only refusal-state exit. There is a CP7 test. The runbook states the rewound-trunk consequence from architecture challenge 1. |
| TBR-R8-005 | accepted | The `BRANCH_PLANNED` `--abandon` local precondition also requires that `HEAD:docs/ai-workflow/WORKFLOW_STATE.json` has no non-terminal entry for the item, and the new `BRANCH_BOUND` precondition requires the same. There is a CP6 test. |
| Usability note | accepted | The (c) refusal names `--abandon` only when its precondition holds, and create-and-switch otherwise, never both. The `bound_item_missing` gate and the trunk-side `BRANCH_BOUND` refusal follow the same rule. |

Registry and mapping: no checkpoint was added, removed or renamed, and no requirement's coverage
changed. Both are regenerated at plan revision 9 with the same 11 checkpoints and 18
requirements, and the embedded table is re-rendered.

### Round 9 (MANUAL_EXTERNAL_PLAN_REVIEW round 1, `REVISE`, reviewed plan revision 9) -> revision 10

The local review had approved revision 9. Every finding of the manual external review was
validated against the repository and the plan text before it was applied. The important finding
and all five optional findings are accepted.

| Finding | Decision | Evidence and resolution |
| --- | --- | --- |
| I1 | accepted | Confirmed: revision 9's registry held CP11 with an external entry condition (a released Workflow 2.6.x), and decision 11 left keep-or-split open, so this milestone's acceptance, the 1.2.0 release and dogfooding waited on an undated release. The user had already decided that this milestone and the Workflow Manager 2.6 milestone finish independently, with integration in a separate follow-up milestone. CP11 is removed from the registry, and CP10 ("full verification under Workflow 2.5.1") is the terminal checkpoint. R8, whose only checkpoint was CP11, is removed from the mapping and moved to the follow-up, rather than left falsely covered. The CP11 section is replaced by "Follow-up milestone boundary", which lists that milestone's expected scope (the 2.6.x upgrade, the measured contract and E1-E5, replacing `controller/evidence.py:170`'s duplicated `resolve_feedback_dir`, re-anchored expected-outcome/state-writer declarations, `VALIDATED_WORKFLOW_RELEASES` and fixtures, 2.5.1-to-2.6.x migration coverage, and any genuinely Workflow-owned branch/base binding) without planning it. The goal, non-goals, boundary table, `merge_trunk`, the `integration_required` gate text, compatibility (`VALIDATED_WORKFLOW_RELEASES` stays `{"2.5.1"}`), rollout (nothing waits for 2.6.x), CP10's README and ROADMAP entries, decisions 10-11 and the artifact declaration now say that this milestone ships and is accepted under Workflow 2.5.1 with the fail-closed `integration_required` gate as its contract. |
| O1 | accepted | Rule 1's four cases now state they are not first-match-then-stop: a case whose writer leaves a non-terminal state (reopen to `PR_OPEN`, reopened-then-merged to `MERGED`, `BRANCH_PLANNED` completion) is followed by the `bound_item_missing` case in the same preflight. A CP7 reopen test covers it. |
| O2 | accepted | The `BRANCH_BOUND` `--abandon` precondition also requires `superseded_prs == []` and `last_observation` null or at `branch_point`. A CP6 test covers each refusal. |
| O3 | accepted | The trunk-side `BRANCH_BOUND` refusal now names both exits (abandon a discarded plan, or switch back and restore a stashed one), because from trunk the precondition cannot tell a discard from a stash. This supersedes round 8's "never both" usability note for this one refusal only; the (c) refusal and the branch-side `bound_item_missing` gate are unchanged, since the branch side can see its own working tree. A CP6 stash fixture is added. |
| O4 | accepted | A bind refused by a leftover branch for an id with an `ABANDONED` record names the branch and `git branch -d milestone/<id>` (or the remote delete). A CP6 re-plan test is added. |
| O5 | accepted | The matrix's `BRANCH_PLANNED` cells say "the first matching refusal row" (forge/common-dir, branch-not-descending, or the catch-all), and the re-bind row and cell repeat that the policy snapshot is re-read at the new branch point. |

Registry and mapping: CP11 is removed and CP10 is renamed; no other checkpoint changes. R8 is
removed. Both are regenerated at plan revision 10 with 10 checkpoints and 17 requirements, and
the embedded table is re-rendered.
