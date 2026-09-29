# Controller squash merges and tag-derived versions: Conventional Commit pull request titles decide the release (Revision 3)

Work item: `workflow-controller-squash-merge-tag-versioning`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `455cef0632a35744060288e71504bf93f1f06830` ("Merge pull request #7 from
RodrigoFAbreu/docs/roadmap-reorganization"), the tip of `origin/main` when this plan was written,
passed explicitly as the operator directed. The previous milestone
(`workflow-controller-workflow-2-6-integration`) was accepted at `65292c0` and merged as `b5332ab`
(tagged `v1.3.0`). The commits after it on `main` are PR #6 (this repository's move to Workflow
2.6.0) and PR #7 (the roadmap reorganisation). Neither is this milestone's work.
Lifecycle authority: this repository's installed Workflow **2.6.0**.
Driving Controller: the installed **1.3.0** package (`workflow-controller --version`: `package
(release v1.3.0; built from b5332abfaaa0)`).
Roadmap slot: `docs/ROADMAP.md` step **C1** of "At a glance", section **11.1 Squash merges and
PR-title versions**.
Released baseline preserved: `workflow-controller 1.3.0` (`v1.3.0`). The code ships as the first
tag-derived release, **1.4.0**, when the cutover pull request is merged (Design H).
Pull request title: `feat: squash merges with release versions derived from pull request titles`

## Goal

Move this repository, and the Controller's generic release and pull-request machinery, from merge
commits and a hand-edited `pyproject.toml` version to the SignalHub model:

1. **Squash merges only.** A milestone pull request lands on `main` as one squash commit, whose
   subject is the pull request title and whose body is the pull request body.
2. **The pull request title is a Conventional Commit.** A required `PR title` check validates it
   with the same code the release uses.
3. **The commit type decides the release.** `feat` gives a minor release, `fix` a patch, and a `!`
   a major. `docs`, `chore` and `ci` merge without a release.
4. **The Git tag is the only version authority.** `pyproject.toml` holds no version, the wheel's
   version is set at build time, and the release computes the next version from the latest release
   tag and the commit types since it.
5. **The existing release safety stays.** Build and verify happen before the tag. Tags are
   immutable. An interrupted publication is resumed (`RESUME`). `abandoned_tags` (v1.1.0) keeps
   working. Releases on `main` stay serialized.
6. **Close-out understands a squash merge** (`MERGED_SQUASHED`). The next milestone is planned
   from `main`'s head, and that base is passed explicitly to `/milestone-plan`.
7. **Cutover.** The switch must never lock out this milestone or its own pull request, and the
   first tag-derived release continues from `v1.3.0`.

These are the user's decisions (2026-09-29). This plan works out how to carry them out; it does not
reopen them.

## Non-goals

- Auto-merge and waiting for the release (C4), CI flake fixes (C2), the settings file and telemetry
  (C3), SignalHub notifications (C5).
- Any change to Workflow or Workflow Manager. Both are consumed as released (2.6.0, Manager 1.0.0).
- Reading a `BREAKING CHANGE:` footer. As in SignalHub, only the title's `!` marks a major
  release, because a squash body varies with settings and pull-request prose.
- Generated release notes. The GitHub release body stays the policy's template (as in 1.3.0).
- Deleting milestone branches. The Controller still never deletes a branch.
- Other forges, or a merge queue.

## Investigation: what the code does today (measured at `455cef0`)

### Where the version is read

The version exists in exactly one place, `pyproject.toml:3` (`version = "1.3.0"`). Everything else
reads it:

| Reader | What it does today |
|---|---|
| `controller/version.py` | `source_version(code_root)` parses `<code_root>/pyproject.toml`'s static `[project].version`. `package_version(code_root)` reads the metadata of the one distribution installed in `code_root` whose `RECORD` owns `controller/__init__.py`. Stdlib only, imports nothing from `controller` (`setup.py` loads it by path). |
| `controller/buildinfo.py` | `SEMVER_RE`, `tag_for_version`/`version_for_tag` (`v{version}`), and `validate_build_info(obj, expected_version=)`. A release build must carry `release_tag == v{version}`. |
| `setup.py` | The `build_py` hook writes `BUILD_INFO.json` with `version = self.distribution.get_version()`, which is setuptools' static read of `pyproject.toml`. `WORKFLOW_CONTROLLER_RELEASE_TAG` in the environment makes it a release build, and a tag that does not match the version fails the build. `DIRTY_SCOPE = ("controller", "pyproject.toml", "setup.py")`. |
| `controller/identity.py` | A source runtime's version is `version.source_version(code_root)` (`resolve_runtime`, `:344-350`). A package runtime's is `package_version`, cross-checked against `BUILD_INFO.json`. An unidentified runtime reports a best effort or `unknown`. A pinned source snapshot's version is read from the snapshot's own `pyproject.toml` (`_snapshot_version`, `:577-600`) and recorded in `SOURCE_PIN.json`. |
| `controller/handoff.py` | `_read_committed_version` reads `HEAD:pyproject.toml`. It is reported in the handoff record only, never a refusal. |
| `controller/cli.py` | `--version` line 1 is `workflow-controller <identity version>`. |
| `tools/release.py` | `version` prints the version the committed policy's version source declares at `HEAD`. `verify-wheel` expects `checked_out_version()` (the working tree's `pyproject.toml`) for both `--tag` and `--local`. `build` and `verify` read the committed version. |
| `.workflow-controller/policy.json` | `release.trigger: "version_change"`, `version_source: {"kind": "pyproject", "path": "pyproject.toml"}`, `tag_format: "v{version}"`, `abandoned_tags: ["v1.1.0"]`. Build env `WORKFLOW_CONTROLLER_RELEASE_TAG={tag}`; verify `verify-wheel {artifact} --tag {tag} --commit {commit}`. |
| `controller/repo_policy.py` | Strict parser: an unknown key at any level, an unknown `kind` or trigger, a missing key or a duplicate key refuses. `RELEASE_TRIGGERS = {"version_change"}`, `VERSION_SOURCES = {"pyproject"}` (refuses a dynamic version), `VERSION_SCHEMES = {"semver"}`. `read_committed_version` reads the version source at a revision. |
| `controller/release_txn.py` | `classify(ctx, C)` reads the version at `C` from the version source, then applies the table rows in order: `COLLISION_TAG_ELSEWHERE`, `ABANDONED_VERSION`, `ABANDONED_TAG_INCONSISTENT`, `ALREADY_RELEASED`, `RELEASE_MISMATCH`, `NO_CHANGE`, `COLLISION_RELEASE_WITHOUT_TAG`, `BASELINE_UNRELEASED`, `RESUME`, `INVALID_TRANSITION`, `RELEASE_DUE`. `build` and `verify` read the committed version; `publish` reclassifies and uses the classification's version. |
| `.github/workflows/main.yml` (generated) | `release-plan` runs `classify` (outputs `state, version, tag, commit`). `build` runs `build`, `verify`, a pipx smoke test comparing `--version` with `workflow-controller $(python3 tools/release.py version)`, and `checksums`. `publish` runs `publish --commit`. |
| `.github/workflows/validate.yml` (generated) | `package` builds a local wheel, runs `verify-wheel --local`, the packaged-runtime tests and the same pipx smoke test. The `plan` and `tests` jobs `pip install -e .` in a **shallow** checkout (no tags). |
| `.github/workflows/ci.yml` (generated) | `pull_request` → `validate`. |

Tests pin the version only indirectly: `tests/fixtures.py:29-37` sets `CONTROLLER_VERSION =
version.source_version(REPO_ROOT)`, and `build_checkout()` copies the real `pyproject.toml` and
`setup.py` into each disposable clone. No test hard-codes `"1.3.0"` as the Controller version. The
tests that assume a static `pyproject.toml` version are:
- `test_version_authority` (`SingleAuthorityTest`, `ReadersTest`, `SourceRuntimeTest`,
  `PinnedChildTest`, `HandoffVersionTest`);
- `test_buildinfo.VersionSourceTest` and the `BuildHookTest` wheel-name assertions;
- `test_release_tools` (`VersionCommandTest`, `CheckedOutVersionTest`, `VerifyWheelTest`);
- `test_release_txn` (the toy `bump()` writes `pyproject.toml`);
- `test_repo_policy` (`test_the_reference_file_is_the_plans_exact_content` pins
  `.workflow-controller/policy.json` to the JSON block in
  `CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md`; the known-kinds assertion
  `trigger in ['version_change']`);
- `test_packaged_runtime` (`_write_version` rewrites the static line);
- `test_trunk_orchestration_e2e.ReleaseHistoryTest`;
- `test_ci_workflows` (the smoke-test model `PIPX_SMOKE`, the `release-plan` outputs, the set of
  generated files).

### The pull request and close-out

- `controller/forge.py` reads `PR_FIELDS = "number,state,isDraft,headRefName,headRefOid,
  baseRefName,isCrossRepository,url,mergedAt,mergeCommit"`, with no title or body. There is no
  `gh pr edit` and no `gh pr merge`. `tests/fake_gh.py` stores `title` and `body` on create but
  never returns them.
- `milestone_branch._discover_on_branch` (`:1097-1101`) creates the Draft PR with **the work-item
  id as its title** and the body "Milestone `<id>` (plan: `<plan_path>`), driven by
  workflow-controller. A human merges it with "Create a merge commit"." plus the marker
  `<!-- workflow-controller: work_item=<id> -->` (written, never read back).
- States (`:65-78`): `BRANCH_PLANNED`, `BRANCH_BOUND`, `PR_PLANNED`, `PR_OPEN`, `READY`, `MERGED`,
  `CLOSED`, `MERGED_REWRITTEN`, `MERGED_BEFORE_ACCEPTANCE`, `PR_CLOSED_UNMERGED`, `ABANDONED`.
  `CLOSED`, `MERGED_REWRITTEN` and `ABANDONED` are terminal.
- **A squash merge is already detected, as an error.** `_merged_handling` (`:901-929`):
  - no acceptance commit in `branch_point..head` gives `MERGED_BEFORE_ACCEPTANCE`;
  - otherwise a PR head that is not an ancestor of the remote trunk gives `MERGED_REWRITTEN`, with
    a one-time `merge_method_rewrote_history` gate ("Disable squash and rebase merging"), no
    switch to the trunk and no fast-forward. Its `merge_commit` is recorded but never checked to
    be on the trunk;
  - otherwise `MERGED`, then `CLOSED` after the switch to the trunk and a fast-forward.
- Gate texts that name "Create a merge commit": `_merge_gate` (`:875-883`),
  `post_acceptance_commits` (`:1141-1145`), `integration_required` (`:1152-1157`) and
  `decision.BRANCH_GATE_TEXTS` (`:566-586`, pinned to `GATE_CODES` by
  `GateTextTest.test_every_gate_has_a_human_gate_text`).
- **The next base.** `decision.decide_no_work_item` (`:1041-1072`) always launches a bare
  `/milestone-plan`. Workflow's no-argument form takes "the completion commit of the previous
  milestone" as the base. After a squash merge that commit is not on `main`, so the next bind
  refuses (`_bind_problems`, `:1224-1227`: `base_commit` must be an ancestor of the trunk tip).
  `Action.command` is literal invocation text, and `classify_selected_action` keys on the command
  token, so `/milestone-plan <sha>` is still the automatic bootstrap triple.
- **The policy is read at `HEAD` of the milestone branch** (`milestone_branch.py:563, 981, 1274,
  1917, 1970`), and every binding stores the raw policy bytes and re-parses them with the running
  parser (`binding_policy`, `:372-381`). Two consequences follow:
  - a new policy key must be optional, or every live binding refuses;
  - **an older Controller refuses any new policy value it does not know.** 1.3.0 refuses an
    unknown trigger or an unknown `pull_request` key, and it refuses it at every lifecycle step.
    This is what shapes the cutover (Design H).
- Workers run with `gh` disallowed while a binding is active, so the Controller itself must set
  the title.

### How the Controller could learn the change type

The registry is written only by Workflow's `generate_registry(work_item_id, plan_revision,
checkpoints)`, which emits exactly `schema_version, work_item_id, plan_revision, checkpoints`. A
Controller-only change cannot add a field there: no Workflow writer can emit it, and a hand edit
breaks plan-approval coverage. The Controller already reads `plan_path` (from `WORKFLOW_STATE.json`)
for the PR body. The plan document is the reviewed, approved artifact, and a plan amendment is the
sanctioned way to change it. So the plan declares the title (Design E).

### SignalHub (read at `a6fcffd`, tag `v2.15.0`)

- **Grammar.** `scripts/release/release.py` defines:
  ```
  ^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9._/-]+)\))?(?P<breaking>!)?: (?P<description>\S.*)$
  ```
  - The type must be one of `feat fix perf refactor docs test build ci chore style revert`.
  - `!` gives a major release, `feat` a minor, and everything else a patch. **In SignalHub every
    type releases.** This repository decided differently: `docs`, `chore` and `ci` release nothing
    (Decision A settles the other types).
  - The squash suffix ` (#N)` is part of the description, so it is accepted.
  - GitHub's default `Revert "…"` title is rejected; it must be retitled `revert: …`.
- **`pr-title.yml`.** It runs on `pull_request: [opened, edited, reopened, synchronize]` with
  `contents: read`. Job `name: Conventional Commit title` (that name is the required context). The
  title reaches the script through an environment variable, never shell interpolation:
  `release.py check-title "$TITLE"`.
- **`release.yml`.**
  - It runs on a push to `main` with `concurrency: release`, `cancel-in-progress: false`.
  - The base is the highest strict `vX.Y.Z` tag in `git tag --merged HEAD`, or `0.0.0` when there
    is none. Every `git log --first-parent <tag>..HEAD` subject is classified, and the highest bump
    wins.
  - Build and check happen first. `gh release create "$NEXT" --target "$GITHUB_SHA"` creates the
    tag last.
  - A non-conforming subject on `main` counts as a patch, with a warning. We fail closed instead
    (Design C).
- **Runtime version.**
  - The Python SDK keeps a `0.0.0.dev0` placeholder that the release rewrites in a temporary copy.
    Its CLI reports "development build" for a `.dev` version.
  - CI builds with `0.0.0+ci`.
  - We keep the strict `MAJOR.MINOR.PATCH` contract instead (Design B).
- **Settings.**
  - The ruleset allows only squash merges, requires linear history and a strict up-to-date
    branch, and requires the `Conventional Commit title` check among others.
  - Squash commits read `feat(client): … (#94)`.

## Invariants

- **I1. One version authority.** A release's version is derived only from release tags (the
  highest one reachable from the released commit) and the Conventional Commit types of the
  first-parent commits since it. No file in the repository holds the Controller's version once the
  cutover lands.
- **I2. The same code decides everywhere.** One parser (`controller/conventional_commit.py`),
  reading the policy committed at the relevant commit, validates a PR title (`check-title`),
  classifies a trunk commit's subject, and validates the title the Controller sets.
- **I3. Release safety is unchanged.** Validation, build and verify come before the tag. No tag
  is ever moved or deleted. `RESUME` completes an interrupted publication at the tag's own commit.
  `abandoned_tags` settles a tag. `main-release` concurrency never cancels a run. Every undecidable
  read refuses.
- **I4. Fail closed on `main`.** A trunk commit under the new trigger whose subject is not a valid
  Conventional Commit, and is not acknowledged in the policy, fails the release run. It is never
  counted as a patch.
- **I5. Strict `MAJOR.MINOR.PATCH` stays.** Every version the Controller reports, builds or tags
  matches `buildinfo.SEMVER_RE`. A non-release build is told apart by `BUILD_INFO.json`
  (`build_origin: local`), not by the version string.
- **I6. Backward compatibility of the policy.** Every policy 1.3.0 accepts, the new parser accepts
  with the same meaning. Every new key is optional. A binding created under the old policy keeps
  working.
- **I7. No lockout.** No commit this milestone makes changes `.workflow-controller/policy.json`,
  so the driving 1.3.0 Controller can run every step of this milestone, including readiness and
  close-out. The policy switch is the separate cutover pull request (Design H).
- **I8. Workflow is untouched.** The Controller invokes Workflow commands exactly as released. The
  squash body never carries a Git trailer, so no `Workflow-*` trailer of a completed item reaches
  `main`'s history.
- **I9. The Controller never merges.** It sets the title and body, marks the pull request ready
  and closes out; a human merges.

## Design

### A. Conventional Commit titles and the policy (CP1)

**New module `controller/conventional_commit.py`.** It is stdlib only and imports only
`controller.errors`. It sits after `repo_policy` in `DEPENDENCY_ORDER`, or before it if
`repo_policy` needs it for validation; the implementer measures the import graph.
- `SUBJECT_RE`: SignalHub's grammar, unchanged. A lowercase type, an optional lowercase scope, an
  optional `!`, then `": "` and a description that starts with a non-space character. The match is
  `fullmatch` on the subject with only its trailing newline removed; leading or trailing spaces are
  not trimmed. A `(#N)` suffix is part of the description.
- `parse(subject) -> Subject(type, scope, breaking, description)`, which raises `InvalidTitle`
  naming the expected shape.
- `bump(subject, change_types) -> "major" | "minor" | "patch" | "none"`:
  - an unknown type raises `InvalidTitle` naming the allowed types;
  - `!` on a type the table maps to `none` raises `InvalidTitle` ("a breaking change must
    release: use `feat!` or `fix!`", Decision A);
  - otherwise `!` gives `major`, and every other type gives the table's value.

**Policy changes (`controller/repo_policy.py`, schema_version stays 1).**
- `RELEASE_TRIGGERS = {"version_change", "conventional_commit"}`.
- For `trigger: "conventional_commit"`:
  - **`version_source` is forbidden.** The tags are the version, and the refusal says so.
  - `change_types` is **required**: a non-empty object from a type (`[a-z]+`) to `minor`, `patch`
    or `none`. `major` is not a table value, because only `!` gives a major release.
  - `bump_overrides` is **optional**: an object from a 40-hex commit to `major`, `minor`, `patch`
    or `none`. It mirrors `abandoned_tags`: an acknowledgement committed on `main`, never guessed.
    - It settles **only** a trunk commit that cannot otherwise be classified: an unparsable subject,
      an unknown type, `!` on a `none` type, or an unparsable committed policy (Design C step 3).
    - It never replaces a decision. An entry naming a range commit whose subject classifies, or a
      legacy commit, makes classification refuse (Design C). A valid title's type, `feat!` or
      `docs` alike, is always authoritative.
  - `version_scheme` must be `semver`, because the bump arithmetic is SemVer's.
- For `trigger: "version_change"`: unchanged. `version_source` is still required, and
  `change_types` and `bump_overrides` are refused.
- `milestone_branches.pull_request.merge_method` is **optional**: `"merge"` (the default, today's
  behaviour) or `"squash"`.
- **Cross-field rule.** When `release.enabled` and `milestone_branches.enabled` are both true, and
  the trigger is `conventional_commit`, `merge_method` must be `"squash"`. Only a squash commit
  carries the title to `main`.
- The dataclasses gain the fields.
  - `Release.version_source_kind` and `Release.version_source_path` become `None` under the new
    trigger. `Release.read_version` refuses to run there, and names the trigger.
  - `MilestoneBranches.merge_method` is added.
- `release.change_types` in this repository's post-cutover policy (Design H) is:

  | Type | Release |
  |---|---|
  | `feat` | minor |
  | `fix`, `perf`, `refactor`, `revert`, `build`, `style` | patch |
  | `docs`, `chore`, `ci`, `test` | none |

  The rule behind it: a type that can change the shipped package releases, and one that cannot
  does not. `feat`/`fix` and `docs`/`chore`/`ci` are the user's decisions; the rest is Decision A.

**`tools/release.py check-title TITLE`.** It reads the policy committed at `HEAD`, which for a
`pull_request` run is the merge ref.
- **Old trigger:** it prints `ok: the release trigger is version_change; the title is not release
  input` and exits 0.
- **New trigger:** it prints `ok: <type> → <bump>`, or `refused: <reason>` on one line with exit 1.
- There is no policy-less exception: a missing policy refuses, as `version` does today.

### B. The version at build time and at run time (CP2)

**One derivation, in `controller/version.py`.** It stays stdlib only and imports nothing from
`controller`.
- `static_pyproject_version(root)`: the static `[project].version`, or `None` when the version is
  dynamic or absent. Malformed TOML, or a static version that is not semver, raises `ValueError`
  as today.
- `tag_version(root, rev="HEAD")`: the highest `v<MAJOR.MINOR.PATCH>` tag in `git -C root tag
  --merged <rev>` (strict `buildinfo`-style matching; other tags are ignored). It returns `"0.0.0"`
  when no such tag is reachable. A `git` failure raises `ValueError`, and so does a `root` that is
  not the top of a Git work tree.
  - **An unborn `HEAD`** (a work tree with no commit yet, such as
    `build_checkout(..., committed=False)` at `tests/test_identity.py:686`) reaches no tag, so it
    gives `"0.0.0"`. It is detected first, with `git rev-parse --verify --quiet <rev>^{commit}`:
    exit 1 with empty output in a valid work tree means "no such commit". `git tag --merged` is
    not run then, because it fails on an unborn `HEAD`. Any other failure of that probe raises
    `ValueError` as above. A `rev` other than `HEAD` that does not resolve still raises.
  - Abandoned tags are not excluded. Only `v1.1.0` is abandoned, and it is below `v1.1.1`, so it
    never wins.
  - The Controller's own tag format is `buildinfo.tag_for_version`'s fixed `v{version}`. It is not
    read from the adopter policy, because the Controller's identity must not depend on a target's
    policy.
- **`source_version(code_root)`** returns the static version when `pyproject.toml` declares one
  (the legacy model: every checkout before the cutover). Otherwise it returns `tag_version(code_root)`.
- **`local_build_version(root)`** is the version a non-release build of `root`'s `HEAD` carries.
  It returns the static version when one is declared. Otherwise it returns `tag_version(root)`, or
  `"0.0.0"` when `root` is not a Git work tree (a build from a copied tree, where provenance is
  already `None`).

  **Why the last release's version, and not a development suffix.** A checkout between two
  releases reports the version of the last release it contains. That is exactly what it reported
  before the cutover, while `pyproject.toml` still said `1.3.0`. It keeps `SEMVER_RE`,
  `tag_for_version`, the wheel filename rule and `validate_build_info` unchanged (I5). The runtime
  line 2 and `BUILD_INFO.json` already tell a local build apart from a release by its commit and
  build origin. A shallow clone with no tags reports `0.0.0`, honestly: CI's `plan` and `tests`
  jobs install editable from a shallow checkout, and nothing there reads the value.

**`setup.py`.**
- When `pyproject.toml` declares `dynamic = ["version"]`, `setup()` receives
  `version=<derived>`:
  - when `WORKFLOW_CONTROLLER_RELEASE_TAG` is set, the version is `buildinfo.version_for_tag(tag)`,
    and a malformed tag fails the build;
  - otherwise it is `version.local_build_version(SOURCE_DIR)`.
- A static version keeps today's path: setuptools reads it, and a mismatching tag fails the build.
- The docstring and `DIRTY_SCOPE` stay. `pyproject.toml` remains a build input.

**Runtime identity (`controller/identity.py`, `controller/handoff.py`).**
- A source runtime reports `version.source_version(code_root)`. The static version wins, then the
  tags. The source probes stay as they are.
- A pinned source snapshot has no `.git`, so `_snapshot_version` cannot derive tags from it.
  Instead, `materialise` computes the version from the **origin checkout** at the snapshot's
  commit (`static_pyproject_version(snapshot)` if the snapshot declares one, else
  `tag_version(origin, rev=<commit>)`), and records it in `SOURCE_PIN.json` as today. A dirty
  snapshot reports the version its commit derives.
- `handoff._read_committed_version` reads `HEAD:pyproject.toml`'s static version when one is
  declared, else `tag_version(source_root, "HEAD")`. It still returns `None` on any failure, and
  remains report-only.
- The package runtime is unchanged: metadata cross-checked against `BUILD_INFO.json`.

**`tools/release.py`.**
- `version` prints the version a **local** build of the checked-out commit carries
  (`local_build_version`). Under the old trigger that is the committed static version, the same
  value as today.
  - The documented meaning changes from "the policy's version" to "the local build's version".
  - The smoke tests use it only for local builds (Design D).
- `verify-wheel --tag TAG` expects `version_for_tag(TAG)`. `verify-wheel --local` expects
  `local_build_version(REPO_ROOT)`. Both still check `--commit`, the digest, the build origin and
  everything else.

**Tests stay valid before and after the cutover.** The cutover changes only `pyproject.toml` and
the policy (Design H), so every test that reads the real repository must pass in both states. The
fixture approach confines the dual-state question to the tests that exercise the dynamic form.
Disposable clones never depend on tags:
- `fixtures.CONTROLLER_VERSION` stays `version.source_version(REPO_ROOT)`.
- **`build_checkout()` always writes a static version line** into the clone's `pyproject.toml`:
  `version = "{CONTROLLER_VERSION}"`, which replaces `dynamic = ["version"]` when the real file is
  post-cutover and changes nothing when it is pre-cutover.
  - A new keyword, `dynamic_version=True`, keeps the real file's dynamic form instead. Only the
    tests that exercise tag derivation pass it, and they create their own tags.
  - `build_checkout()` **never tags** a clone. No release fixture built on a clone meets a
    real-looking `v{CONTROLLER_VERSION}` tag, so no push or bare origin made from a clone sees an
    unsettled ancestor tag (`RESUME`/`BASELINE_UNRELEASED`).
- **No test copies the real `.workflow-controller/policy.json` into a clone.** A test that needs a
  policy writes a named fixture policy for the trigger it exercises:
  - `fixtures.LEGACY_POLICY`, the trunk plan's reference content;
  - `fixtures.CONVENTIONAL_POLICY`, this plan's post-cutover block.
  - `test_release_tools`'s wheel class (`tests/test_release_tools.py:337-344`) copies the real
    policy today and asserts, then rewrites, the static `version = "{VERSION}"` line. It writes
    `LEGACY_POLICY` instead. Its static-line assertion holds in both states, because
    `build_checkout()` writes the static line. The other copy into a clone,
    `tests/test_release_tools.py:286`, is converted the same way. The tests that only read the
    real policy in place, such as `tests/test_ci_workflows.py:676`, stay as they are. The
    post-cutover suite run below proves them in both states.
- Tests that rewrite the version, such as `test_packaged_runtime._write_version` and the handoff
  "newer version" cases, rewrite the clone's static line, which every clone now has.
- The tests that exercise the dynamic form (`test_version_authority`, the `tag_version` and
  `setup.py` build cases) build their own tagged repositories, and never rely on the real
  repository's state.

A consistency test asserts that the real repository is in exactly one of two states:
- **pre-cutover:** a static version with the `version_change` trigger;
- **post-cutover:** a dynamic version with the `conventional_commit` trigger.

**The post-cutover suite run.** From CP2 on, each checkpoint's exit includes a run of the full
sharded suite (`python3 tools/run_tests.py`) in a **scratch clone** of the checkpoint's head. It is
a full clone, so it carries the real tags. The cutover's two files are applied to it as one extra
commit (Design H, step 4). The command and its result go into the implementation bundle's test
results. The run happens at CP2, CP3, CP5 and CP6, and again in CP7's rehearsal. A failure is fixed
in the checkpoint that owns the failing test, not deferred to the terminal checkpoint.

### C. Release classification from commit types (CP3)

The trigger of the policy committed at `C` selects the model, as the whole policy already does.
The new trigger only changes **how the version at `C` is computed**. Every existing row then
applies unchanged:
- under `version_change`, the version is read from the version source (today);
- under `conventional_commit`, it is computed as follows:

1. **The base.** Among the matching remote tags that are ancestors of `C` (already computed as
   `ancestors`), the base is the highest by the scheme key. With none, the base version is
   `0.0.0` and has no tag.
2. **The unsettled base comes first.** If the base tag has no published release (none, or a draft)
   and is not in `abandoned_tags`, the version at `C` is the base version. The existing rows then
   yield **`RESUME` at the base tag's own commit**, whatever the commits since it contain.
   - The bump since it is released by the next run: the next push to `main`, or `main.yml`'s
     `workflow_dispatch`.
   - This replaces the old model's resolution for an interrupted release ("set the version back"),
     which no longer exists.
   - A **lower** unsettled tag still gives `BASELINE_UNRELEASED`, and `abandoned_tags` still
     settles it.
3. **The range.** `git rev-list --first-parent <base tag commit>..C` (all of `C`'s first-parent
   history when there is no base tag), read through new read-only `gitrepo` helpers. For each
   commit:
   - **An unparsable historical policy refuses.** The policy committed at a range commit may exist
     but fail the running parser, for example after a hand-broken commit in an adopter's history.
     Classification then **refuses** (`_refuse`, as for every other undecidable read, I3/I4). The
     refusal names the commit and the parser's error. It is never read as a legacy commit, which
     could silently drop a release. The remedy it names is the same as for an unclassifiable
     subject: add the commit to `release.bump_overrides` in `C`'s policy. Such a commit's override
     is then used without its policy.
   - **Legacy commit:** the policy committed at that commit is absent, or parses with
     `trigger: "version_change"`. It contributes **no bump**. Its release was decided by its own
     rule, and a pending legacy version bump, if any, is superseded by the first Conventional Commit
     after it. In this repository that covers #6, #7 and this milestone's own merge commit.
   - **Conventional commit:**
     - the commit's own subject is classified with `conventional_commit.bump`, against the
       `change_types` committed at that commit. A subject that classifies is authoritative;
     - an unparsable subject, an unknown type, or `!` on a `none` type is **unclassifiable**.
       `C`'s policy's `bump_overrides[commit]` settles it when present. Otherwise it classifies `C`
       as **`INVALID_SUBJECT`**, a new failing state. It names the commit, its subject and the fix:
       add the commit to `release.bump_overrides` in a pull request. The whole run fails, and
       nothing is tagged (I4).
   - **An override never replaces a decision.** A `bump_overrides` entry in `C`'s policy that
     names a range commit whose subject classifies, or a legacy commit, **refuses** (`_refuse`). The
     refusal names the commit, its subject, what the commit already classifies as, and the fix:
     remove the entry in a pull request. The run fails and nothing is tagged. An entry naming a
     commit outside the range is ignored, because it settled an earlier range.
4. **The version at `C`.** The highest bump in the range applied to the base. `major` gives
   `X+1.0.0`, `minor` gives `X.Y+1.0`, `patch` gives `X.Y.Z+1`, and `none` keeps the base version.
   - **No base tag and no bump** (an empty range, or one where every commit is legacy or `none`)
     classifies `C` as **`NO_CHANGE`** straight away, before the rows. The detail is "no release
     tag is reachable and no commit since the start of history releases". The version output is
     `0.0.0`, and there is no tag commit. Without this rule, the unchanged rows would find no tag,
     no release and no ancestors, and give **`RELEASE_DUE` 0.0.0** (`release_txn.py:399`). An
     adopter who opted in before their first tag would then tag and publish `v0.0.0` for a
     `docs:` pull request, and a pushed tag is immutable.
   - `NO_CHANGE` is already in `SUCCESS_STATES`, and `main.yml` builds only for `RELEASE_DUE` and
     `RESUME`. So the run succeeds, builds nothing and tags nothing, with no workflow change.
5. **The rows.** The unchanged rows run on that version:
   - a `none` range → the base tag, already published at an ancestor → `NO_CHANGE`;
   - a release commit re-run at the tagged commit → `ALREADY_RELEASED`;
   - an abandoned base → `ABANDONED_VERSION`;
   - a new version with no tag → `RELEASE_DUE`;
   - a new version whose tag exists off `C`'s history → `COLLISION_TAG_ELSEWHERE`;
   - a new version with a release but no tag → `COLLISION_RELEASE_WITHOUT_TAG`;
   - a new version listed in `abandoned_tags` → `ABANDONED_TAG_INCONSISTENT` (fail).

   `INVALID_TRANSITION` cannot occur under the new trigger. A range with a bump gives a version
   above the highest ancestor tag by construction. A range with no bump either has a base tag,
   which the earlier rows settle (`NO_CHANGE`, `RESUME`, `ABANDONED_VERSION`), or has none, which
   step 4's rule settles. The row stays as a defensive one.

`INVALID_SUBJECT` is placed first among the rows, before any forge read beyond what classification
already does. Its outputs are the base version and tag. It is not in `SUCCESS_STATES`.

**`build` and `verify` take the classified version.**
- `release_txn.build(ctx, commit, version)` and `verify(ctx, commit, version)`: under
  `version_change` a `version` that differs from the committed version refuses (defence in depth).
  Under `conventional_commit` it is the version the build injects through the policy's build
  environment (`{tag}`).
- **`tools/release.py build` and `verify` read the classified version from the environment
  variable `RELEASE_VERSION`**, not from a new argument. Their command lines stay byte-identical to
  1.3.0's (`python3 tools/release.py build`, `python3 tools/release.py verify`).
  - Under `conventional_commit` a missing or empty `RELEASE_VERSION` refuses. Under
    `version_change` it is optional, and a value that differs from the committed version refuses.
  - The value comes from `release-plan`'s `version` output (Design D).
  - **Why not an argument: a `RESUME` can target older tooling.** `main.yml`'s build job checks
    out the target commit (`main.yml:71-73`) and runs *that commit's* `tools/release.py`. A
    `RESUME` targets the base tag's own commit (step 2 above), which can predate this milestone:
    `v1.3.0` is at `b5332ab`, whose `build` and `verify` parsers take no option
    (`tools/release.py:230-234` at `455cef0`). A `--version` argument would fail argparse there,
    and an interrupted pre-upgrade publication could never complete.
  - 1.3.0's tooling ignores the unknown variable and builds its own committed static version. That
    is the classified version, because a `RESUME`'s version is its tag's, and the tag was created
    from that static version. Any target whose own committed policy is `conventional_commit`
    carries tooling that reads the variable, because that trigger ships with it.
- `publish` is unchanged: it reclassifies with fresh reads, and refuses a target or version that
  differs from what was built. `local_artifacts(version)` names the wheel by version, so a
  different version fails there.

### D. CI workflows (CP4)

All generated by `tools/ci_workflows.py`; the committed YAML stays the golden.

- **New `.github/workflows/pr-title.yml`.** `name: PR title`, `on: pull_request: types: [opened,
  edited, reopened, synchronize]`, `permissions: contents: read`, one job `title` with
  `name: PR title` (the required check context). Steps: pinned `actions/checkout`
  (`persist-credentials: false`; the default depth of 1 is enough, because only the merge ref's
  own committed policy is read), pinned `actions/setup-python` 3.12, then `python3 tools/release.py check-title
  "$TITLE"` with `env: TITLE: ${{ github.event.pull_request.title }}`. The title is never
  interpolated into the script. The actions are pinned to the same SHAs as `main.yml`
  (`ACTION_PINS`).
- **`main.yml` `build` job.**
  - The job gains `env: RELEASE_VERSION: ${{ needs.release-plan.outputs.version }}`. The `build`
    and `verify` steps keep their 1.3.0 command lines byte for byte, so the job also runs against a
    `RESUME` target that carries 1.3.0's tooling (Design C).
  - The pipx smoke test compares with `workflow-controller $RELEASE_VERSION`, the classified
    version, instead of `tools/release.py version`. This is correct for both triggers (under
    `version_change` the classified version is the committed version) and for an older target.
- **`validate.yml` `package` job.** The smoke test keeps `python3 tools/release.py version`, now
  "the local build's version". `verify-wheel --local` is unchanged.
- `ci.yml` is unchanged. `tests.test_ci_workflows` gains the new file (the set of generated files,
  the triggers, the permissions, the env-passed title, the pins) and the `main.yml` changes.

### E. The pull request title and body (CP5)

**The title comes from the plan.** A plan declares its pull request's title as one line of the form
``Pull request title: `<Conventional Commit>` `` (at the start of a line, the title in backticks),
exactly once.
- The Controller reads the plan through a **new committed read**, and extracts the line with a
  strict anchored regex:
  - `plan_path` is taken from `HEAD`'s committed state (`committed_state(ctx, "HEAD")`);
  - the plan text is `HEAD:<plan_path>`, read through a read-only `gitrepo` helper.

  It does not reuse the body's read. That read takes only `plan_path`, and takes it from the
  **working tree's** `WORKFLOW_STATE.json` (`worktree_state(ctx)`, `milestone_branch.py:1096`).
  It never reads the plan text. An uncommitted edit to the plan or the state therefore never
  changes the title. In squash mode, the body's `plan_path` also comes from the committed state,
  so the title and the body name the same plan. Merge mode keeps today's working-tree read, byte
  for byte (I6).
- Zero lines, or more than one, means "no declared title". The value is validated with
  `conventional_commit.bump` against the `change_types` of the policy committed at `HEAD` when
  its trigger is `conventional_commit`. Otherwise (squash mode with the release disabled) only the
  grammar is checked, with any lowercase type.
- The line sits in the plan's header block, so the reviewers approve it with the plan, and a plan
  amendment changes it.
- This plan declares its own. Its header line above is inert for this milestone's own pull request,
  which 1.3.0 opens, and becomes the cutover pull request's title (Design H).

**Only in `merge_method: "squash"` mode.** With `"merge"` (or no key), every title, body and gate
text is byte-identical to today (I6).

- **Creation.** The Draft PR is titled with the declared title when it is valid, and otherwise
  with the work-item id. A PR with an invalid title fails its required `PR title` check early and
  visibly, and a Draft PR cannot be merged anyway.
- **Title sync on every `PR_OPEN` step (squash mode).** Every lifecycle step on a `PR_OPEN`
  record already re-reads the pull request (`_verified_pr` in `_branch_cells`,
  `milestone_branch.py:651-652`). After that read, when the plan at `HEAD` declares a valid title
  and the PR's title differs, the Controller edits the title (`gh pr edit <n> --title`) and then
  continues as before.
  - This keeps the required `PR title` check green through the implementation, and through an
    amendment that changes the declared title, so the "PR CI green before functional review" rule
    (Verification) is not broken by a stale title.
  - The edit is idempotent: a restart re-reads the PR and finds nothing to change. It is never
    made on a `READY` record, whose PR stays exactly as readied.
  - The body is not synced here. Its "Accepted at" line exists only at readiness.
- **Readiness (squash mode).** The sync runs **before** readiness condition 7 (`_checks_gate`,
  `milestone_branch.py:1165-1169`), right after conditions 4-6. It must run first: condition 7
  returns `checks_failing` for any failing check, including the `PR title` check that only this
  sync can fix. In the other order, readiness would deadlock with the wrong remedy ("fix the
  failures on the branch"), and `pr_title_invalid` could never be reached whenever
  `ready_requires_green_checks` is true, which is this repository's post-cutover policy.
  1. **Title decision.** When the plan declares a valid title and the PR's title differs, the
     Controller edits it. When the plan declares none, the PR's current title is kept if it is
     valid, so a human may set it on GitHub. Otherwise readiness gates **`pr_title_invalid`**,
     before any checks are read. Its remedy is to set a valid title on GitHub, because the plan
     can no longer be amended after acceptance. The problem is visible much earlier: a Draft PR
     created without a valid declared title carries the work-item id, so its required `PR title`
     check is red from the first push, while `/request-plan-amendment` is still available.
     When the plan declares a valid title, it wins over a title a human set on GitHub: the plan is
     the reviewed record.
  2. **Body sync.** The body is re-rendered with the acceptance commit and edited when it differs
     (`gh pr edit <n> --body`).
  3. **An edit ends the step.** When step 1 or step 2 edited anything, readiness returns
     `checks_pending` ("the pull request's title or body was just updated; its checks re-run") and
     stops. It does not go on to condition 7 or to `gh pr ready` in the same invocation. The next
     step finds nothing to edit, and condition 7 samples the re-run `PR title` check afresh. So
     `READY` is never written on a check that was never sampled after the edit.
  4. With nothing edited, condition 7 and the rest of readiness run unchanged.
- **The body (squash mode).** It becomes the squash commit's body:
  ```
  Milestone `<id>`, planned in `<plan_path>`, driven by workflow-controller.
  Accepted at <acceptance commit> on `<branch>`; merge with "Squash and merge".

  <!-- workflow-controller: work_item=<id> -->
  ```
  - The first line exists from creation. The "Accepted at" line is added by the readiness body
    sync.
  - **No line may parse as a Git trailer.** A test runs `git interpret-trailers --parse` over the
    rendered body and requires empty output (I8).
- **Gate texts (squash mode).** `merge_pull_request`, `post_acceptance_commits`,
  `integration_required` and the matching `decision.BRANCH_GATE_TEXTS` say "Squash and merge"
  instead of "Create a merge commit". `pr_title_invalid` joins `GATE_CODES` and gets its text.
- **Forge (`controller/forge.py`, `tests/fake_gh.py`).**
  - `PR_FIELDS` gains `title,body`, and `PullRequest` gains `title` and `body`.
  - New `edit_pr(number, *, title=None, body=None)` runs `gh pr edit <n> --repo … [--title T]
    [--body B]`, then re-reads the PR with `view_pr` and refuses unless the re-read shows the new
    values.
  - `fake_gh.py` returns `title` and `body` in `PR_JSON_FIELDS` and implements `pr edit`.
- **Forge arguments.** Titles and bodies travel as argv elements, never through a shell. The
  forge's existing argv discipline holds.

### F. Close-out after a squash merge, and the next base (CP6)

**`MERGED_SQUASHED`.** A new non-terminal state.
- `TRANSITIONS`: `PR_OPEN|READY → MERGED_SQUASHED → CLOSED`.
- The same-state rewrite is admitted like the other states.
- It is added to `MERGED_PR_HANDLING`.

**Only in squash mode.** Detection runs only when the binding's policy
(`binding_policy(record)`, the snapshot taken at the branch point) has `merge_method: "squash"`.
Under `"merge"`, the default and every 1.3.0 policy, `_merged_handling` is unchanged: a merged head
that is not on the trunk is `MERGED_REWRITTEN`, with today's record and gate, byte-identical to
1.3.0, whatever the merge actually was. A squash in merge mode therefore stays a rewrite, as it is
today. This is what keeps the Migration notes' promise to `version_change` adopters.

In squash mode, `_merged_handling` (after the unchanged `MERGED_BEFORE_ACCEPTANCE` check)
classifies a merged PR whose head `h` is **not** an ancestor of the remote trunk as
`MERGED_SQUASHED` only when all of these hold. Otherwise it stays `MERGED_REWRITTEN`, with today's
gate:
1. `pr.merge_commit` is non-null, and is an ancestor of, or equal to, the fetched remote trunk tip;
2. the merge commit has exactly one parent `p`;
3. its subject is exactly the PR's title followed by ` (#<number>)`, GitHub's squash subject under
   the "Pull request title and description" default that Design H step 3 configures. A bare title
   does not qualify: it is also what GitHub's other default gives for a one-commit PR (the commit's
   own message), and what a rebase merge of a commit titled like the PR gives. A human who edits
   the squash message in GitHub's merge dialog therefore gets `MERGED_REWRITTEN`, and the guide
   says not to edit it: the title is what the release reads;
4. **its content is the reviewed content.** Its tree equals the tree of `h` when `p` is an
   ancestor of `h` (the ordinary case: `main` did not move). Otherwise, on the
   `integration_required` path, it equals the tree `git merge-tree --write-tree p h` produces. A
   conflict, or a different tree, is not a verified squash.
   - `merge-tree` writes only unreferenced objects into the object database. It is read-only for
     refs, the index and the working tree.
   - **The Git floor.** `merge-tree --write-tree` needs Git 2.38. The Controller supports Git from
     2.31 (`controller/workflow_contract.py:676`). Only the integration path needs it. The
     ordinary path compares two trees and needs nothing new.
   - **Merge drivers are never run.** `merge-tree` runs the merge machinery, so a `merge=<name>`
     attribute with a configured `merge.<name>.driver` would run an external program. Before
     calling it, the Controller lists the configured drivers (`git config --get-regexp
     '^merge\..*\.driver$'`, every scope). If any is configured, `merge-tree` is not run. An
     attribute naming a driver that is not configured uses Git's built-in text merge and runs no
     program, so it needs no check.
   - **How each outcome lands**, so that a terminal state is written only on a decided answer:
     - a conflict (exit 1), a different tree, or a configured merge driver: **not verified**, so
       `MERGED_REWRITTEN` with today's gate. These are answers about this merge. The driver case
       is one because the Controller declines to verify content that would take a program to
       produce;
     - Git older than 2.38 (read from `git version` before the call), or any other failure of
       `merge-tree` (exit other than 0 or 1, or unparsable output): the step **refuses** and
       writes nothing, naming the cause and, for the version, the 2.38 floor. This is an
       undecidable read, not an answer (I3). `MERGED_REWRITTEN` is terminal, so writing it here
       would make a Git upgrade useless. After the upgrade or the fix, the next step verifies.
5. **it is not a rewrite of `h`.** A rebase merge keeps each commit's author, author date and full
   message, and its last commit, the PR's merge commit, is the rewrite of `h`. A merge commit whose
   author name, author email, author date and full message all equal `h`'s (one `git log -1
   --format` read of each) is therefore treated as a rebase and gives `MERGED_REWRITTEN`, whatever
   conditions 1-4 say.

**A state that cannot be told apart is not verified.** Conditions 3 and 5 settle the case the
other conditions cannot: a one-commit rebase whose subject is the title has the same parent, tree
and subject as a squash. It passes condition 3 only if `h`'s own subject already carries the PR's
number, and then fails condition 5, because it carries `h`'s identity. A genuine squash that
happened to reproduce `h`'s identity byte for byte would also get `MERGED_REWRITTEN`. The rule
errs towards today's gate, never towards a squash it cannot verify.

The fields recorded are `merge_commit` and `merged_head` (= `h`), as for `MERGED`. The mode also
shapes titles and gate texts.

**Close-out from `MERGED_SQUASHED`** mirrors `MERGED`, with the squash commit standing in for the
merged head wherever trunk membership is checked:
- **On the branch.** It needs a clean tree and `tip == merged_head`. The squash commit must be on
  the remote trunk. Then the Controller switches to the trunk, fast-forwards it and writes
  `CLOSED`. A branch commit beyond `merged_head` gates `unmerged_commits`, as today.
- **On the trunk.** The `unmerged_commits` check runs first, then the squash-commit-on-remote-trunk
  check, then `CLOSED`. The trunk start then gates `fast_forward_trunk` as today.
- The local `milestone/<id>` is left in place. `git branch -d` refuses it because it is unmerged
  by ancestry; the guide says to use `git branch -D` after close-out.
- `status`, `inspect`, `explain`'s prediction (`_predict_on_branch`, `_predict_on_trunk`) and the
  binding lines name the new state.

**The explicit base.** When milestone branches are enabled and the trunk start has passed,
`decide_no_work_item` names `/milestone-plan <trunk tip>`:
- the trunk tip is the full 40-hex `HEAD`, which the trunk start has just proved equal to
  `origin/<trunk>`;
- without a policy (or with branches disabled) it still names bare `/milestone-plan`, so
  `tests/golden/no_policy_lifecycle.json` is byte-unchanged;
- the triple stays `(NO_PHASE, None, "/milestone-plan")`, and the reason text names the base.

This holds for both merge methods. After a merge commit the tip is that merge commit, which is a
valid base too. The rule gives every milestone `base_commit == branch_point`, and it is what
Workflow's one-argument `<base-sha>` form exists for. The job record carries the literal command,
and the expected-outcome and job-validation tables key on the command token, so they need no new
row. CP6 confirms this with a test, and adds the argument-bearing command to whatever the tables
key on if the measurement says otherwise.

**`MERGED_BEFORE_ACCEPTANCE` under squash.**
- The `--new-pr` continuation's next PR meets `integration_required`, because `main` holds a squash
  commit the branch does not descend from.
- The documented procedure (mark it ready, then "Squash and merge") still converges. The final
  squash is verified with the `merge-tree` rule (condition 4).
- The guide says this; no new mechanism is added.

### G. Documentation (CP7)

- **`docs/guide/ci-and-releases.md`.**
  - "Releasing" is rewritten for the tag-derived model: the trigger, the type table, the `!` rule,
    the range and base, `INVALID_SUBJECT` and `bump_overrides`, "the unsettled base resumes first",
    and the updated states table.
  - Maintainer step 1 ("Bump the version") is gone.
  - The new `PR title` check.
  - "Repository settings": squash-only, the squash commit title and message, the ruleset's allowed
    merge method and required checks (Design H).
  - A short "Cutover from the version-file model" section.
- **`docs/guide/milestone-branches.md`.**
  - The squash flow diagram, the plan's title declaration, title and body sync at readiness,
    `pr_title_invalid`, `MERGED_SQUASHED` and close-out.
  - The explicit base for the next `/milestone-plan`, `git branch -D` for the leftover branch, and
    `MERGED_BEFORE_ACCEPTANCE` under squash.
  - "Create a merge commit" becomes mode-dependent.
- **Other guides.** `docs/guide/runtime.md` (a source runtime's version comes from the tags),
  `installation.md` ("From a checkout" versions), `concepts.md`, `troubleshooting.md` (`NO_CHANGE`
  wording, `INVALID_SUBJECT`, `pr_title_invalid`) and `development.md` (building from a checkout).
- **`README.md`.** The "Milestone branches, pull requests and releases" bullets: squash merges, the
  title-derived release, and "a `docs`/`chore`/`ci` title publishes nothing".
- **ADR `docs/adr/0007-tag-derived-versions-and-squash-merges.md`.** It warrants one: it moves the
  version authority, changes the merge contract and adds a policy trigger.
  - It records Designs A-C, E, F and H, and the rejected alternatives: a registry field, a
    `.dev` suffix, lenient patch counting, and flipping the policy inside the milestone.
  - It supersedes ADR 0003's "`pyproject.toml` is the single version authority" and its
    merge-commit-only rule. ADR 0003 gains a pointer; its text is otherwise unchanged.
- **`docs/releases/1.4.0.md`** (Decision D), and the `docs/README.md` releases row and ADR row.
- **`docs/ROADMAP.md` section 1.5.** The note that its "single human-maintained version
  authority" rule is superseded by 11.1 (C1) and ADR 0007.
- **`CLAUDE.md`.** One line: "A milestone plan declares its pull request title (see
  `docs/guide/milestone-branches.md`)." Every planning worker reads `CLAUDE.md`, so plans carry
  the line without a Workflow change.

### H. Cutover

**The constraint (measured).** The driving Controller is the installed 1.3.0 package. It reads
`.workflow-controller/policy.json` strictly, at the milestone branch's `HEAD`, at every lifecycle
step, and refuses an unknown trigger or key. If any commit of this milestone changed the policy
to the new trigger, 1.3.0 would refuse every later step of this milestone: reviews, acceptance,
readiness and close-out. So:

**This milestone never changes `.workflow-controller/policy.json` or `pyproject.toml`'s version
line (I7).** It ships all the code, with both triggers supported. At its merge, `main` still runs
the legacy model. The switch is one small pull request, prepared and rehearsed by CP7. The steps,
in order:

1. **This milestone's PR** is opened by 1.3.0 (titled with the work-item id) and merged by the
   human with **"Create a merge commit"**, as today; the settings still allow only merge commits.
   - `main.yml` at the merge commit runs the **new** tools against the **legacy** policy and a
     static `1.3.0`. It classifies **`NO_CHANGE`**, and nothing is released. CP7 rehearses exactly
     this.
   - The new `pr-title.yml` runs on this PR too. It passes under the legacy policy ("not release
     input") and is not yet required.
2. **1.3.0 closes the milestone out** (`MERGED` → `CLOSED`). `main`'s policy is still the legacy
   one, so 1.3.0 accepts it.
3. **Settings, with the user's approval** (the user makes them, or explicitly authorises them):
   - Settings → General → Pull Requests: allow **squash merging only** (merge commits and rebase
     off). Default squash commit message: **"Pull request title and description"**. Auto-merge and
     head-branch deletion are unchanged.
   - Ruleset "Main Protection":
     - the allowed merge method becomes **squash**;
     - add the required check **`PR title`**, which exists on `main` since step 1, so no open pull
       request is locked out;
     - add **"Require linear history"**;
     - the existing required checks, the review-thread rule and "not up to date is allowed" stay.

     No pull request is in flight at this moment, and this milestone's PR is already merged.
4. **The cutover pull request.** It is prepared by hand, or by the orchestrator on the user's
   explicit authorisation, like the 2.6.0 `chore/workflow-2.6.0` PR. It changes exactly two files,
   to the content below:
   - `pyproject.toml`: `version = "1.3.0"` is replaced by `dynamic = ["version"]`;
   - `.workflow-controller/policy.json`: the post-cutover policy below.

   Its title is this plan's declared title, **`feat: squash merges with release versions derived
   from pull request titles`**. `PR title` passes (the merge ref carries the new policy), and the
   full validation passes in both states (Design B). The human **squash-merges** it.
5. **The first tag-derived release.** `main.yml` classifies the squash commit.
   - The base is `v1.3.0` (published). The range is #6, #7 and the milestone merge commit (all
     legacy, no bump), then the cutover squash commit (`feat` → minor). The result is
     **`RELEASE_DUE` 1.4.0**.
   - The build injects `v1.4.0`, then verify, the smoke test (`workflow-controller 1.4.0`), the tag
     and the release follow.
6. **Install 1.4.0** (`pipx install --force`, after `status` shows `active: none`). Until then,
   1.3.0 refuses lifecycle commands on this repository, because it cannot read the new policy. No
   work item is in flight, so nothing is lost, and the refusal names the unknown trigger.
7. **The next milestone (C2)** is launched by 1.4.0's bootstrap as `/milestone-plan <main tip>`,
   with the cutover squash commit as the base.

**Post-cutover policy (the exact CP7 rehearsal content):**

```json
{
  "schema_version": 1,
  "trunk": {"branch": "main", "remote": "origin"},
  "forge": {"kind": "github", "repository": "RodrigoFAbreu/workflow-controller"},
  "milestone_branches": {
    "enabled": true,
    "branch_format": "milestone/{work_item_id}",
    "pull_request": {"draft": true, "ready_requires_green_checks": true, "merge_method": "squash"}
  },
  "release": {
    "enabled": true,
    "trigger": "conventional_commit",
    "change_types": {"feat": "minor", "fix": "patch", "perf": "patch", "refactor": "patch",
                     "revert": "patch", "build": "patch", "style": "patch",
                     "docs": "none", "chore": "none", "ci": "none", "test": "none"},
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

`tests/test_repo_policy.py`'s reference test accepts `.workflow-controller/policy.json` when it
equals either the trunk plan's reference block (pre-cutover) or this block (post-cutover), and no
other content. It also parses this block from this plan and asserts that it validates, so the
rehearsed content can never drift from what CP1's parser accepts.

**Why not something else:**
- **Flipping the policy inside the milestone** locks 1.3.0 out of its own milestone (the
  constraint above).
- **Merging this milestone's PR by squash** before the settings change would leave 1.3.0 recording
  `MERGED_REWRITTEN` (a one-time gate, no close-out switch). The merge-commit path closes out
  cleanly.
- **A version bump to 1.4.0 in this milestone** is a last hand edit, which decision 4 rules out.
  The cutover PR's `feat` title produces 1.4.0 from the tag instead, so the first tag-derived
  release continues from `v1.3.0`, as asked.

**If the rehearsed prediction breaks.** For example, someone releases between steps 1 and 5.
Classification computes whatever the tags and titles say, which is correct by construction.
`docs/releases/1.4.0.md` is then renamed in the cutover PR (Decision D).

## Checkpoints

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Conventional Commit titles and the policy schema: controller/conventional_commit.py (SignalHub's grammar, the (#N) suffix accepted, unknown types and a breaking change on a none type refused, bump from the policy's change_types), repo_policy admitting trigger conventional_commit with required change_types, optional bump_overrides and no version_source, the optional pull_request.merge_method with the squash cross-field rule, every 1.3.0 policy still admitted with the same meaning, tools/release.py check-title reading the committed policy, the post-cutover policy block of the plan validated and the reference test accepting exactly the pre- or post-cutover content | - | 2 | 1 |
| CP2 | Tag-derived Controller version: controller/version.py static-else-tag derivation (highest reachable v-semver tag, 0.0.0 without one), setup.py supplying a dynamic version from the release tag or the local derivation, source runtime and pinned-snapshot versions derived from the origin checkout's tags, handoff's committed version, tools/release.py version and verify-wheel expectations, and fixtures and version tests valid both before and after the cutover, with a scratch-clone proof that a dynamic pyproject builds a local wheel at the tag version and a release wheel at the injected tag | CP1 | 3 | 1 |
| CP3 | Release classification from commit types: release_txn computing the version at C from the highest ancestor tag and the first-parent range's Conventional Commit bumps (legacy-policy commits contribute none, bump_overrides from C's policy settling only unclassifiable commits and refusing an override of a classifiable or legacy one, INVALID_SUBJECT failing closed, the unsettled base tag resumed first), every existing row unchanged for both triggers, build and verify taking the classified version from RELEASE_VERSION with their 1.3.0 command lines unchanged, so a RESUME at a target carrying 1.3.0's tooling still builds, read-only gitrepo range helpers, and toy-history and end-to-end release-history tests including the cutover history | CP2 | 3 | 1 |
| CP4 | CI workflows: tools/ci_workflows.py model gaining pr-title.yml (PR title job on opened, edited, reopened and synchronize, read-only, pinned actions, title passed through env to check-title), main.yml's build job passing release-plan's version as RELEASE_VERSION with build and verify run lines byte-identical to 1.3.0's and the release smoke test comparing with it, validate's local smoke test on the local build version, rendered YAML committed and test_ci_workflows updated | CP3 | 1 | 1 |
| CP5 | Pull request title and body in squash mode: forge reading title and body and editing them (gh pr edit, re-read verified), fake_gh support, the plan's declared title read at HEAD and validated against the committed change_types, creation with the declared title, readiness title and body sync with the new pr_title_invalid gate, a trailer-free squash body, mode-dependent gate texts, and merge mode byte-identical to 1.3.0 | CP1 | 3 | 1 |
| CP6 | Squash close-out and the explicit base: the MERGED_SQUASHED state and transitions, verified-squash detection in squash mode only, merge mode byte-identical to 1.3.0 (merge commit on the trunk, one parent, the title with its (#N) suffix as subject, tree equal to the head or to merge-tree on the integration path, not a rewrite of the head), close-out from the branch and from the trunk, status, inspect and explain naming it, and the bootstrap naming /milestone-plan with the trunk tip when milestone branches are enabled, with the no-policy golden byte-unchanged and an end-to-end squash lifecycle through the next trunk start | CP5 | 3 | 1 |
| CP7 | Documentation, ADR 0007, cutover rehearsal and full verification (terminal): the guides, README, CLAUDE.md, docs/README.md, ROADMAP 1.5 note and docs/releases/1.4.0.md, ADR 0007 with the ADR 0003 pointer, a scratch-clone rehearsal of the milestone merge (NO_CHANGE), of the cutover pull request (tests passing in the post-cutover state, RELEASE_DUE 1.4.0 over the real tag topology, a release build and verify-wheel at v1.4.0) and of a RESUME of an interrupted v1.3.0 publication built with v1.3.0's own tooling, and the full sharded, packaged-runtime, CI-workflow and golden verification | CP4, CP6 | 2 | 1 |

### CP1 -- Conventional Commit titles and the policy schema

Design A. Files:
- `controller/conventional_commit.py` (new) and `controller/__init__.py` (`__all__`);
- `controller/repo_policy.py`, `tools/release.py` (`check-title`);
- `tests/test_conventional_commit.py` (new), `tests/test_repo_policy.py`,
  `tests/test_release_tools.py` (`RetiredSubcommandsTest`'s subcommand set gains `check-title`);
- `tests/test_package_structure.py` (`DEPENDENCY_ORDER`).

Tests:
- **Grammar.** Every SignalHub accepted and rejected example: `Feat:`, `feature:`, `feat:add`,
  `feat: ` (empty description), `feat add`, `Revert "feat: x"`, `feat():`, `feat: x (#12)`, a
  scope, `!` after the scope, and trailing or leading whitespace.
- **Bumps.** Every row of the table. `!` on each releasing type; `!` on a `none` type refused.
- **Policy.**
  - Every 1.3.0 policy variant still parses to the same values.
  - The new trigger with and without each key: `version_source` refused, `change_types` required,
    malformed `change_types` and `bump_overrides` keys and values, and `version_scheme` other than
    semver.
  - `merge_method` values, and the cross-field rule.
  - The known-kinds refusal message lists both triggers.
  - The plan's post-cutover block parses. The reference test accepts either exact content and
    refuses anything else.
- **`check-title`.** Both triggers, valid and invalid titles, the one-line refusal, a missing
  policy, and the policy read from `HEAD`, not from the working tree.
- **`read_version` under the new trigger.** `Release.read_version` refuses, and the refusal names
  the trigger.

Exit:
- every existing test passes;
- `.workflow-controller/policy.json` is byte-unchanged;
- `Release.read_version` refuses under the new trigger, naming it (the test above). Its callers
  (`release_txn.py:330, 430, 441`, `tools/release.py:269`) still call it unconditionally at this
  checkpoint. They stop in CP2 and CP3, and CP3's exit asserts that none is left.

### CP2 -- tag-derived Controller version

Design B. Files:
- `controller/version.py`, `controller/identity.py`, `controller/handoff.py`, `setup.py`,
  `tools/release.py` (`version`, `verify-wheel`);
- `tests/fixtures.py`, `tests/test_version_authority.py`, `tests/test_buildinfo.py`,
  `tests/test_release_tools.py`, `tests/test_packaged_runtime.py`, `tests/test_identity.py`,
  `tests/test_handoff.py`, and `tools/test_timings.json` for renamed classes.

Tests:
- **Derivation.** `tag_version` on temporary repositories: no tag gives `0.0.0`; the highest of
  several; a tag on a side branch is ignored; `v01.2.3`, `1.2.3` and `v1.2.3-rc.1` are ignored; a
  lightweight and an annotated tag are both read; a non-repository or a failing `git` refuses;
  an unborn `HEAD` gives `0.0.0`, and a source runtime on an unborn `HEAD` with a dynamic
  `pyproject.toml` reports `0.0.0`.
- **Fixtures.**
  - `build_checkout()` writes a static line in both states, and keeps the dynamic form only with
    `dynamic_version=True`.
  - It never creates a tag. A test asserts that `git tag` is empty in a fresh clone.
  - No test copies the real `.workflow-controller/` into a clone.
- **Static first.** A static `pyproject.toml` still wins (the legacy tests keep passing).
- **Runtimes.**
  - A source runtime and a pinned snapshot report the derived version: clean, dirty, dynamic and
    static.
  - A package runtime is unchanged.
  - The handoff's committed version is read from tags, from `HEAD`, not from the working tree.
- **Builds.**
  - A `pip wheel` of a scratch clone with a dynamic `pyproject.toml` and a `v2.3.4` tag is named
    `2.3.4`.
  - With `WORKFLOW_CONTROLLER_RELEASE_TAG=v2.4.0` it is a release build at `2.4.0`. A malformed tag
    fails the build.
  - With no tag the version is `0.0.0`.
  - An editable install writes no `BUILD_INFO.json`, as today.
- **Wheel verification.** `verify-wheel --tag` expects the tag's version, and `--local` expects
  `local_build_version`.
- **The consistency test.** The real repository is exactly pre-cutover or exactly post-cutover.
- **Both states.** The whole of `test_packaged_runtime` runs once more in a scratch clone with the
  cutover's `pyproject.toml` applied (a one-off verification, recorded in the implementation
  bundle's test results, not a permanent test).

Exit:
- every existing test passes;
- `--version`, `status` and every `BUILD_INFO.json` report the same values as before in the
  pre-cutover state;
- the full sharded suite passes in the post-cutover scratch clone (Design B's "post-cutover suite
  run").

### CP3 -- release classification from commit types

Design C. Files:
- `controller/release_txn.py`, `controller/gitrepo.py` (read-only first-parent range and subject
  helpers), `tools/release.py` (`build`/`verify` reading `RELEASE_VERSION`);
- `tests/test_release_txn.py`, `tests/test_release_tools.py`,
  `tests/test_trunk_orchestration_e2e.py` (`ReleaseHistoryTest`), `tests/test_gitrepo.py`.

Tests (toy histories on a scratch bare origin with the fake forge, both triggers):
- **Types.** `feat` gives `RELEASE_DUE` minor; `fix` patch; `feat!` major, including from 0.x;
  `docs`, `chore`, `ci` and `test` give `NO_CHANGE`.
- **Ranges.**
  - A range mixing `docs` and `feat` releases the minor.
  - A skipped middle run is covered: two pushes, one run, and the highest bump wins.
  - A `(#N)` suffix is accepted.
- **`INVALID_SUBJECT`.**
  - A merge commit subject gives `INVALID_SUBJECT`, and so do an unknown type and `docs!`. Each
    names the commit and fails.
  - A `bump_overrides` entry committed later settles the commit.
- **Overrides never replace a decision.** An override naming a range commit with a valid `feat!`
  subject (overridden to `none`), one with a valid `docs` subject (overridden to `major`), and one
  naming a legacy commit each refuse, naming the commit, its subject and its classification. No
  tag or release is created. An override naming a commit before the base tag is ignored.
- **Legacy commits.** Commits without a policy, or under `version_change`, contribute nothing.
  - This includes the exact shape of this repository's history since `v1.3.0`: two legacy merge
    commits and a legacy milestone merge, then a `feat` squash, gives `RELEASE_DUE` 1.4.0.
  - At the milestone merge alone, under the legacy policy, it gives `NO_CHANGE`.
- **The unsettled base.**
  - A tag pushed and publication interrupted, then a `fix` merged, gives `RESUME` at the tag. After
    publication, the next run gives `RELEASE_DUE` of the patch.
  - A draft base behaves the same.
  - A lower unsettled tag gives `BASELINE_UNRELEASED`, and acknowledging it in `abandoned_tags`
    settles it.
- **The other rows.** `ABANDONED_VERSION` with `v1.1.0`; `COLLISION_TAG_ELSEWHERE` for a new
  version's tag on a side branch; `COLLISION_RELEASE_WITHOUT_TAG`; `ALREADY_RELEASED` on re-run;
  `RELEASE_MISMATCH`; no base tag at all gives `0.1.0` for a `feat`.
- **No base tag and no bump.** A history with no tags and only `docs`/`chore` commits (and one with
  only legacy commits) gives `NO_CHANGE` at `0.0.0`. It is a success state. `build` is not run, and
  no tag or release is created (the forbidden-argv and forge assertions).
- **An unparsable historical policy.** A range commit whose committed policy exists but fails the
  parser refuses, naming the commit and the parse error. The same commit listed in `C`'s
  `bump_overrides` classifies.
- **Build and verify.** Under the new trigger they refuse without `RELEASE_VERSION`, or with an
  empty one. Under the old trigger they build without it, and a value that differs from the
  committed version refuses. Their parsers still accept the bare 1.3.0 command lines, with no new
  argument.
- **`RESUME` at a target carrying 1.3.0's tooling.** A toy history whose base tag's commit
  commits a `tools/release.py` stub with 1.3.0's `build`/`verify` parser (no options) and a
  static version, with a later `feat` squash and the base's publication interrupted, classifies
  `RESUME` at the tag's commit. The build job's `build` and `verify` steps, read from the
  generated `main.yml` (not retyped), run there with the job's environment and succeed. The real
  `v1.3.0` tooling is exercised in CP7's rehearsal, because CI's test checkout is shallow and has
  no tags.
- **Publish.** It refuses a build of another version. The concurrent-tag races run again under the
  new trigger.
- The existing `version_change` tests pass unchanged, apart from the `build` and `verify`
  signature.

Exit:
- the module teardown's forbidden-argv assertion still holds (no `--force` and no tag or release
  deletion);
- every existing test passes;
- no caller of `Release.read_version` runs under the new trigger. `classify`, `build` and `verify`
  branch on the trigger, and `tools/release.py version` uses `local_build_version` (CP2);
- the full sharded suite passes in the post-cutover scratch clone.

### CP4 -- CI workflows

Design D. Files:
- `tools/ci_workflows.py`, `.github/workflows/pr-title.yml` (new, rendered),
  `.github/workflows/main.yml` (re-rendered);
- `tests/test_ci_workflows.py`.

Tests:
- `--check` is clean, and the set of generated files includes `pr-title.yml`.
- `pr-title.yml`:
  - its triggers and types, `contents: read`, and the job name `PR title`;
  - the title is passed only through `env`: no `${{` appears in any `run:`;
  - pinned actions, and `persist-credentials: false`.
- `main.yml`:
  - the build job's `env` carries `RELEASE_VERSION` from `release-plan`'s `version` output;
  - the `build` and `verify` steps' `run` lines are byte-identical to 1.3.0's
    (`python3 tools/release.py build`, `python3 tools/release.py verify`), so a `RESUME` target
    with 1.3.0's tooling accepts them;
  - the smoke test compares with `$RELEASE_VERSION`;
  - the outputs are unchanged, `state, version, tag, commit`.
- `validate.yml`'s package smoke test is unchanged in text.

Exit: `python3 tools/ci_workflows.py --check` passes, and `test_ci_workflows` passes.

### CP5 -- pull request title and body in squash mode

Design E. Files:
- `controller/forge.py`, `controller/milestone_branch.py`, `controller/decision.py` (gate texts);
- `tests/fake_gh.py`, `tests/test_forge.py`, `tests/test_pull_request_lifecycle.py`,
  `tests/test_milestone_branch.py`.

Tests:
- **Forge.** It reads `title` and `body`. `edit_pr` runs the exact argv, re-reads, and refuses
  when the re-read disagrees.
- **Title.**
  - The declared title is extracted: exactly one line; zero or two lines mean none; a backtick
    inside or a missing backtick is refused; the title is validated against the committed
    `change_types`; the plan is read at `HEAD`, not from the working tree.
  - Creation uses the declared title, or the id when the declaration is invalid or missing.
  - The plan and its `plan_path` are read from `HEAD`'s commit. An uncommitted change to the plan's
    title line, or to the working tree's `plan_path`, changes nothing.
  - **`PR_OPEN` sync.** A step on a `PR_OPEN` record edits a differing title to the declared one,
    and a restart finds nothing to edit. A `READY` record's title is never edited. Merge mode never
    edits.
  - Readiness keeps a valid human-set title when none is declared.
  - **Readiness order** (all with `ready_requires_green_checks: true`):
    - an **invalid current title** with a **valid declared title**, and the `PR title` check
      failing, converges without a human. The first step edits the title and returns
      `checks_pending`. The next step, with the re-run check passing, marks the PR ready;
    - a **missing declaration** with an **invalid** current title, and the `PR title` check
      failing, gates `pr_title_invalid`, not `checks_failing`;
    - an edit of the title or of the body ends the step at `checks_pending`. `gh pr ready` is
      not run in the same invocation (the fake forge's call log), and no `READY` is written.
- **Body.**
  - The "Accepted at" body sync.
  - `git interpret-trailers --parse` gives empty output for every rendered body.
- **Merge mode.** Every title, body and gate text is byte-identical to 1.3.0's for a policy
  without `merge_method` (the existing assertions stay, unchanged).
- **Gate texts.** Squash mode names "Squash and merge". `GateTextTest` covers `pr_title_invalid`.

Exit:
- every existing test passes unchanged for merge mode;
- the full sharded suite passes in the post-cutover scratch clone.

### CP6 -- squash close-out and the explicit base

Design F. Files:
- `controller/milestone_branch.py`, `controller/decision.py`, `controller/job.py` (the bootstrap
  hand-off, where the trunk tip is passed), `controller/cli.py` (only if the binding view needs
  it);
- `tests/test_milestone_branch.py`, `tests/test_pull_request_lifecycle.py` (`MATRIX` rows from each
  side, fixtures and the squash cells), `tests/test_trunk_orchestration_e2e.py`,
  `tests/test_decision.py`, and `tests/test_job_validation.py` (property 5 with the argument-bearing
  bootstrap).

Tests:
- **The state model.** `STATES`, the class partition and the exact transition table.
- **Detection.**
  - A real `git merge --squash` merge whose subject is the PR's title with ` (#<number>)` gives
    `MERGED_SQUASHED` under a squash policy.
  - **Merge mode keeps 1.3.0's meaning.** The same squash under a policy without `merge_method`
    gives `MERGED_REWRITTEN`, and its record and gate text are byte-identical to 1.3.0's.
  - A rebase merge (multi-commit, and single-commit with its own subject) gives `MERGED_REWRITTEN`
    with today's gate.
  - **A one-commit rebase whose subject equals the PR title** gives `MERGED_REWRITTEN` under both
    the legacy and the squash policy. So does one whose subject already carries ` (#<number>)`
    (condition 5), and a squash commit given `h`'s author, author date and message.
  - A squash commit that is not on the trunk, a squash with a different tree, a bare title
    subject, or a subject that differs from the title each gives `MERGED_REWRITTEN`.
  - The integration path: `main` moved, and `merge-tree` equals the squash tree, gives
    `MERGED_SQUASHED`.
  - The integration path's failures:
    - a `merge-tree` conflict gives `MERGED_REWRITTEN`;
    - a configured `merge.<name>.driver` gives `MERGED_REWRITTEN`, and the driver program (a marker
      script) never runs;
    - a Git reported as older than 2.38, or a `merge-tree` that fails with another exit (both
      simulated through the runner), refuses. It writes nothing, and a later step with a working
      Git verifies the squash.
- **Close-out.**
  - From the branch: clean, tip equal to the merged head, switch, fast-forward, then `CLOSED`.
  - From the trunk: the same, plus `unmerged_commits` and `dirty_tree`.
- **Reporting.** `status`, `inspect` and `explain` name `MERGED_SQUASHED`.
- **Bootstrap.**
  - With a policy it names `/milestone-plan <40-hex trunk tip>`, automatic, and property 5 is clean.
  - Without one it is bare, and `no_policy_lifecycle.json` is byte-unchanged (`--check`).
- **End to end.** A squash lifecycle in a disposable repository: a Draft PR with the declared
  title, readiness, a squash merge with `fake_gh`, close-out, then the next trunk start, whose
  bootstrap names the squash commit. Workflow's real `/milestone-plan` argument resolution is not
  run (no worker); the test asserts the launched command text.
- **`MERGED_BEFORE_ACCEPTANCE --new-pr` under squash.** It converges through `integration_required`
  and a verified squash.

Exit:
- every existing test passes;
- the goldens are byte-unchanged (every generator run with `--check`);
- the full sharded suite passes in the post-cutover scratch clone.

### CP7 -- documentation, ADR 0007, cutover rehearsal and full verification (terminal)

Designs G and H. Files:
- `docs/guide/ci-and-releases.md`, `milestone-branches.md`, `runtime.md`, `installation.md`,
  `concepts.md`, `troubleshooting.md`, `development.md`;
- `README.md`, `CLAUDE.md`, `docs/README.md`, `docs/ROADMAP.md` (the 1.5 note only);
- `docs/adr/0007-tag-derived-versions-and-squash-merges.md` (new), and a pointer in
  `docs/adr/0003-trunk-branch-pr-release-orchestration.md`;
- `docs/releases/1.4.0.md` (new).

**Rehearsal.** This runs in a scratch clone with a scratch bare origin that holds the real tags
`v1.1.0`-`v1.3.0` at their real commits, and the fake forge seeded with the real release set
(v1.1.0 abandoned, the others published).
1. Build a `--no-ff` merge of the milestone head into `origin/main`, as GitHub's merge commit would
   be. `tools/release.py classify` gives `NO_CHANGE` at 1.3.0.
2. On top of it, apply the cutover's two-file change as one squash commit titled with the declared
   title plus ` (#N)`:
   - `check-title` accepts the title;
   - the full sharded suite passes in that state;
   - `classify` gives `RELEASE_DUE` 1.4.0 with target the squash commit;
   - `build` and `verify` with `RELEASE_VERSION=1.4.0` pass, and `verify-wheel --tag v1.4.0`
     passes;
   - the pipx smoke test prints `workflow-controller 1.4.0`.
3. **An interrupted pre-upgrade release completes through `RESUME`.** With `v1.3.0`'s release
   removed from the fake forge (the tag pushed, the publication interrupted), `classify` at the
   cutover squash commit gives `RESUME` at `b5332ab`, version 1.3.0. In a worktree of the scratch
   clone at `b5332ab`, which carries 1.3.0's own tooling, the build job's `build`, `verify`, pipx
   smoke-test and `checksums` steps, read from the generated `main.yml` (not retyped), run with
   the job's environment (`RELEASE_VERSION=1.3.0`). They pass, and the smoke test prints
   `workflow-controller 1.3.0`. `publish --commit b5332ab` against the fake forge then completes
   the release, and the next `classify` gives `RELEASE_DUE` 1.4.0.
4. The rehearsal commands and their outputs go into the implementation bundle's test results.

Verification: the full sharded run (`python3 tools/run_tests.py`), the packaged-runtime tests
(`CONTROLLER_REQUIRE_PACKAGING_TESTS=1`), `python3 tools/ci_workflows.py --check`, and every golden
generator with `--check`.

Exit: the rehearsal matches Design H's prediction. If it does not, the plan's prediction is wrong,
and that is a blocking finding, not a documentation fix.

## Decisions for the reviewer and the user

- **A. The types the user did not name** (recommended as tabled in Design A).
  - `feat` minor and `fix` patch, and `docs`, `chore` and `ci` releasing nothing, are decided.
  - For the rest, the rule is "releases if it can change the shipped package": `perf`, `refactor`,
    `revert`, `build` and `style` are patch, and `test` is none.
  - `!` on a `none` type is refused rather than read as a major release. A breaking change must
    ship, and a `docs!:` major would be a surprise.
  - The alternatives were SignalHub's "everything else is a patch", or `style` as none. Either is a
    one-line policy change, since the table lives in the policy.
- **B. Fail closed on an unclassifiable trunk subject** (`INVALID_SUBJECT`, settled with
  `bump_overrides`). This repository fails closed everywhere else, and `required_linear_history`
  plus squash-only make it rare. SignalHub counts such a commit as a patch, with a warning.
  `bump_overrides` settles only such a commit; it never overrides a subject that classifies.
- **C. The cutover sequence** (Design H): this milestone never touches the policy or the version
  line. Its PR is merged with a merge commit, as the last one. Settings change after close-out.
  A two-file cutover PR, titled `feat: …`, is squash-merged and produces 1.4.0. **Two actions need
  the user:** the settings change (step 3), and authorising the cutover PR (step 4), which is a
  pull request outside the Workflow.
- **D. Release notes.**
  - This milestone writes `docs/releases/1.4.0.md`, because the cutover's version is determined and
    rehearsed. If an unexpected release intervenes, the cutover PR renames the file.
  - From 1.4.0 on, a milestone cannot know its version before its merge. The guide says a
    milestone's notes are its PR body, which becomes the squash commit body. `docs/releases/` then
    keeps only notes written after a release, if any.
  - The recommended alternative, deferred, is generating the GitHub release body from the squash
    bodies since the base tag. It is out of scope here.
- **E. The title lives in the plan**, as a header line, and is not an operator argument or a
  Controller-owned file. It is reviewed and approved with the plan, changed only by an amendment,
  and needs no Workflow change. The cost is a regex over the plan text, which is kept strict.
- **F. The explicit base is always passed** when milestone branches are enabled, not only after a
  squash. Every milestone then gets `base_commit == branch_point`. It changes the automatic
  bootstrap command for policy repositories only. Previously the operator typed the base by hand.
- **G. The source-runtime and local-build version is the last release reachable, or `0.0.0`.**
  There is no `.dev` suffix, and `SEMVER_RE` stays strict. The consequence is that a checkout ahead
  of v1.4.0 reports `1.4.0` until the next tag. The runtime line and `BUILD_INFO.json` distinguish
  it, exactly as before the cutover.

## Review dispositions

### Round 1 (`LOCAL_MODEL_PLAN_REVIEW`, bundle `d5c25786…`, `REVISE`) → revision 2

Each finding was checked against the code at `455cef0` before it was applied.

| Finding | Disposition | Where |
|---|---|---|
| LPR-R1-001 (important) readiness syncs the title after condition 7 | **Accepted.** Confirmed: `_readiness` runs `_checks_gate` (`milestone_branch.py:1165-1169`) before `gh pr ready`, and `_checks_gate` returns `checks_failing` for any failing check (`:1184-1192`). The sync and the `pr_title_invalid` decision now run before condition 7, after conditions 4-6. An edit ends the step at `checks_pending`, without `gh pr ready`. The title is also synced on every `PR_OPEN` step: each one already re-reads the PR (`:651-652`), so the sync costs no extra forge read. | Design E; CP5 tests |
| LPR-R1-002 (important) no base tag and an all-`none` range releases `v0.0.0` | **Accepted.** Confirmed: with no tag, no release and no ancestors, every earlier row misses and `release_txn.py:399` returns `RELEASE_DUE`. A new pre-row rule gives `NO_CHANGE` at `0.0.0`. It is already in `SUCCESS_STATES` (`release_txn.py:52`), and `main.yml` builds only for `RELEASE_DUE`/`RESUME` (`main.yml:68, 104`), so no new state is needed. The `INVALID_TRANSITION` claim is corrected. | Design C step 4; CP3 tests |
| LPR-R1-003 (optional) dual-state fixtures under-specified; post-cutover suite only at CP7 | **Accepted, with the reviewer's recommended approach.** Clones get a static version line (dynamic only on request), and never a tag. No test copies the real policy into a clone (`tests/test_release_tools.py:286, 340`). An unborn `HEAD` gives `0.0.0`. The post-cutover full-suite run is an exit of CP2, CP3, CP5 and CP6. | Design B; CP2, CP3, CP5 and CP6 exits; Verification |
| LPR-R1-004 (optional) `merge-tree`'s Git floor and merge drivers | **Accepted, with one refinement.** A conflict, a different tree or a configured merge driver (drivers are never run) gives `MERGED_REWRITTEN`, as recommended. Git older than 2.38, or any other `merge-tree` failure, **refuses** and writes nothing, rather than writing `MERGED_REWRITTEN`. `MERGED_REWRITTEN` is terminal (`milestone_branch.py:85`), so writing it on an environmental failure would make a Git upgrade useless. It would also contradict I3's "every undecidable read refuses". | Design F condition 4; CP6 tests |
| LPR-R1-005 (optional) Design E names a plan reader that does not exist | **Accepted.** Confirmed: the body reads only `plan_path`, from `worktree_state(ctx)` (`milestone_branch.py:1096`). The title now uses a new committed read (`HEAD`'s committed state, then `HEAD:<plan_path>`). | Design E; CP5 tests |
| LPR-R1-006 (optional) an unparsable historical policy | **Accepted.** It refuses, naming the commit and the parse error. It is settled with `bump_overrides`, and an overridden commit's policy is not read. | Design C step 3; CP3 tests |
| LPR-R1-007 (optional) CP1's exit cannot be checked at CP1 | **Accepted.** Confirmed: `release_txn.py:330, 430, 441` and `tools/release.py:269` call `read_committed_version` unconditionally at `455cef0`. CP1's exit is now "`read_version` refuses under the new trigger, naming it (a test)". The "no caller" claim moves to CP3's exit. | CP1 and CP3 exits |

No checkpoint was added, removed or renamed. The registry and the mapping are regenerated at
revision 2 with the same checkpoints and requirements.

### Round 2 (`MANUAL_EXTERNAL_PLAN_REVIEW`, bundle `f3273183…`, `REVISE`) → revision 3

Each finding was checked against the code at `455cef0` before it was applied.

| Finding | Disposition | Where |
|---|---|---|
| Important 1: `RESUME` can target tooling that rejects `--version` | **Accepted.** Confirmed: the build job checks out `needs.release-plan.outputs.commit` (`main.yml:71-73`) and runs that commit's `tools/release.py`, and 1.3.0's `build`/`verify` subparsers take no option (`tools/release.py:230-234`). The version now reaches `build`/`verify` through the job environment (`RELEASE_VERSION`), and their command lines stay byte-identical to 1.3.0's. 1.3.0's tooling ignores the variable and builds its static version, which is the `RESUME` version. A toy-history test (CP3), a `main.yml` text test (CP4) and a rehearsal against the real `v1.3.0` commit (CP7 step 3) cover it. | Design C "`build` and `verify`", Design D, CP3, CP4 and CP7, Migration |
| Important 2: overrides can defeat a valid title | **Accepted.** Confirmed: Design C step 3 let `bump_overrides[commit]` win before the subject was parsed. An override now settles only an unclassifiable commit (unparsable subject, unknown type, `!` on a `none` type, unparsable committed policy). An entry naming a range commit whose subject classifies, or a legacy commit, refuses and names the fix. | Design A, Design C step 3, Decision B, CP3 tests |
| Important 3: squash detection ignores `merge_method` and misses a titled one-commit rebase | **Accepted.** Confirmed: `_merged_handling` (`milestone_branch.py:900-927`) writes `MERGED_REWRITTEN` for any head off the trunk, and Design F applied detection in every mode. Detection now runs only under `merge_method: "squash"` of the binding's policy, so merge mode is byte-identical to 1.3.0. In squash mode the subject must be the title with ` (#<number>)`, and a new condition 5 treats a merge commit carrying `h`'s author, author date and full message as a rebase. A state that cannot be told apart gives `MERGED_REWRITTEN`. | Design F, CP6 tests, Migration |

The three missing tests the review names are added: the `RESUME` at 1.3.0 tooling (CP3 and CP7),
the override on a valid `feat!` and `docs` subject (CP3), and the titled one-commit rebase under
both policies (CP6). No checkpoint was added, removed or renamed. The CP3, CP4, CP6 and CP7
registry descriptions, and the R5, R6 and R8 requirement descriptions, are updated to match, and the
registry and the mapping are regenerated at revision 3.

## Open questions

None that block planning. Two facts are measured during implementation, each with a fallback
named:
- whether the job-validation and expected-outcome tables key purely on the command token (CP6
  adds the argument form if not);
- the exact import order for `conventional_commit` (CP1).

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-squash-merge-tag-versioning-artifacts.json` starts
from `generate_artifacts_declarations(..., work_item_type="product")` and is fitted to this plan's
footprint, as the previous Controller milestones' declarations were.

**Plan stage.**
- Protected: this plan, its registry and its mapping.
- It inherits the template exclusions and adds these as excluded implementation content:
  `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`, `docs/releases/`,
  `pyproject.toml`, `setup.py` and `docs/README.md`.

**Implementation stage.**
- Protected prefixes: `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`,
  `docs/releases/`, and the inherited `docs/adr/`.
- Protected paths:
  - `README.md`, `docs/README.md`, `pyproject.toml`, `setup.py`;
  - the four rendered workflows (`validate.yml`, `ci.yml`, `main.yml`, and the new `pr-title.yml`,
    a tombstone until CP4 writes it);
  - `CLAUDE.md`, moved from the inherited exclusion because CP7 changes it and a planning-worker
    instruction is reviewed content;
  - the artifacts file itself.
- `.workflow-controller/` and `pyproject.toml` stay protected although this milestone must leave
  them byte-unchanged (I7). A change would be reviewed, and a test asserts it did not happen.

`docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md` and `docs/ai-workflow/` stay excluded, as narrative
and bookkeeping. `.github/workflows/workflow-conformance.yml` stays under the inherited `.github/`
exclusion: the Workflow Manager owns it. `scripts/`, `.claude/commands/` and `.workflow-manager/`
keep their inherited exclusion, and this milestone never edits this repository's installed
Workflow.

## Verification

- **Per checkpoint.** The named test modules, then the full sharded run
  (`python3 tools/run_tests.py`) before each checkpoint commit. `PYTHONPATH=.` is never set.
- **CP2, CP3, CP5 and CP6.** The full sharded suite also runs in the post-cutover scratch clone
  (Design B), recorded in the test results.
- **CP2 and CP7.** The packaged-runtime tests with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`.
- **CP4 and CP7.** `python3 tools/ci_workflows.py --check`.
- **CP6 and CP7.** Every `tests/golden/generate_*.py --check`. Without `--check` a generator
  rewrites its golden.
- **CP7.** The cutover rehearsal (Design H), and a check that `.workflow-controller/policy.json`
  and `pyproject.toml`'s version line are byte-identical to the base commit
  (`git diff 455cef0 -- .workflow-controller/policy.json pyproject.toml` is empty).
- **CI.** PR CI on the milestone's Draft PR must be green before functional review. Review rounds
  read the PR's checks first.

## Migration / data-integrity notes

- **Bindings.**
  - Existing and closed bindings store 1.3.0-era policy bytes. They re-parse unchanged under the
    new parser (I6).
  - `MERGED_REWRITTEN` records stay terminal and keep their meaning.
  - No binding record is rewritten.
- **Tags.** No existing tag, release or asset is touched. `v1.1.0` stays abandoned. The first
  tag-derived release, `v1.4.0`, is created by the unchanged publish path.
- **Adopters.** Another repository on `version_change` keeps exactly today's behaviour, including
  merge-commit gate texts and close-out: without `merge_method: "squash"` a squash or rebase merge
  is still `MERGED_REWRITTEN` (Design F). Only a policy that opts into `conventional_commit` and
  `squash` changes anything.
- **An interrupted release across the upgrade.** A tag pushed by 1.3.0 whose publication never
  completed is resumed by the new `main.yml` at its own commit, with that commit's 1.3.0 tooling
  (Design C, CP7 rehearsal step 3).
- **Downgrade.** A 1.3.0 Controller refuses the post-cutover policy. Rolling back after the
  cutover means reverting the cutover PR, as a `revert:` squash, which is a patch release, or
  running 1.4.0+. The guide says so.
