# Continuous integration and releases

[Back to the documentation map](../README.md)

## Continuous integration

`.github/workflows/validate.yml` is the single definition of required
validation. It is called, never triggered directly, and has four jobs,
each on `ubuntu-latest` with Python 3.12 and read-only permissions:

- `plan`: plans the whole CI selection with
  `tools/run_tests.py plan --profile ci --ci-placement`: every test
  `python3 -m unittest discover -s tests -t .` loads, plus the seven frozen
  Workflow conformance suites, in duration-balanced shards, from the
  committed `tools/test_timings.json`. Its outputs are the shard indexes,
  their count and the plan's digest, and it uploads `plan.json`;
- `tests`: one matrix job per planned shard (`fail-fast: false`). Each
  recomputes the plan from the checked-out commit, refuses to run unless
  its digest equals `plan`'s, runs its shard and uploads its result
  record and log as the `results-<i>` artifact. A shard that does not
  pass prints its failing tests with their traceback tails in its own
  step log;
- `tests-result`: runs even when a shard failed or never started, and
  aggregates every shard's result. It passes only if every planned test
  ran exactly once and passed; a missing plan or a missing shard result
  fails it. It uploads the run's durations as the `timings-ci` artifact.
  `tools/test_shards.py`'s CI placement keeps `tests.test_packaged_runtime`
  (run by `package`) and `tests.test_integration_disposable_repo` (which
  needs the live `claude` binary and real spend) out of the plan;
- `package`: builds the wheel, verifies it with
  `tools/release.py verify-wheel --local`, runs the package-placed
  modules (`tests.test_packaged_runtime`), then installs the wheel with
  pipx and checks `--version`'s first line.

Two workflows call it:

- `ci.yml` runs on every pull request, milestone branches included (they
  are validated through their Draft PR). A newer push to the same ref
  cancels the run in progress. Pushes to other branches without a pull
  request are not validated.
- `main.yml` runs on every push to `main` and on `workflow_dispatch`
  (no inputs: it classifies the current tip of `main`). After `validate`,
  its `release-plan` job classifies the commit, and its `build` and
  `publish` jobs run only when a release is due or resumable (see
  [Releasing](#releasing)). Its runs never cancel one another (concurrency group
  `main-release`). GitHub keeps one running and one pending run per
  group, so a burst of merges can skip a middle commit's run; that loses
  nothing, because classification compares with the last release tag in
  the commit's history, not with the previous push. `publish` is the
  only job with write permission, and every action outside `validate` is
  pinned to a commit SHA.

`workflow-conformance.yml` is managed by the Workflow Manager and is
untouched. It still runs the seven conformance suites serially, in one
job, on every pull request (about 5 minutes), so it, not `validate`, is
the floor on when all of a pull request's checks finish. `validate` runs
the same suites through the planner as well; the acceptance-matrix suite
(about 3 minutes on CI) is its own largest shard. `release.yml` is gone:
pushing a `v*` tag by hand triggers nothing.

The CI plan uses the `ci` profile: about 180 s of work per shard, at
least 2 and at most 16 shards, from the committed `tools/test_timings.json`
only. A stale or missing entry costs wall time (the shard count and the
balance), never coverage. The plan is
deterministic: each `tests` job recomputes it and refuses to run on a
digest mismatch, so the matrix carries only shard indexes. Every
`tests-result` summary states the coverage check's verdict. A failing
run's summary also names each failing test, its shard, the shard's log,
the artifacts to download (`results-<i>` holds the log, `test-plan` the
`plan.json` the replay command reads) and the reproduction commands.

`pr-title.yml` (check `PR title`) runs on every pull request when it is
opened, reopened or edited, and on every push to it. It runs
`python3 tools/release.py check-title "$TITLE"` against the policy
committed on the pull request's merge ref, with read-only permissions and
the title passed through the environment, never into the script. Under
the `conventional_commit` trigger it prints `ok: <type> → <bump>` (for
example `ok: feat → minor`), or fails with one line naming what is wrong
with the title (see [Releasing](#releasing)). Under `version_change` the
title is not release input, and the check passes. A title edited on
GitHub re-runs it. The same command works locally:
`python3 tools/release.py check-title "fix: a typo in the gate text"`
reads the policy committed at `HEAD`.

The four workflow files `ci.yml`, `main.yml`, `validate.yml` and
`pr-title.yml` are generated. Edit the model in
`tools/ci_workflows.py`, then run `python3 tools/ci_workflows.py --write`;
`python3 tools/ci_workflows.py --check` (and a test) fails when a
committed file differs from the model.

The checks are `validate / plan`, `validate / tests (<i>)`,
`validate / tests-result`, `validate / package` and `PR title`. See
[Repository settings](#repository-settings) for which of them `main`
requires.

## Releasing

The repository policy's `release` section says how releases are made:
the trigger, the tag format, the build and verify commands, the
artifacts and the publication target. The generic transaction is
`controller/release_txn.py`; `tools/release.py` is this repository's
thin CLI over it. There are two triggers:

- **`conventional_commit`**: the release tags are the version, and the
  Conventional Commit subjects of the commits on `main` decide the next
  one. Nothing in the repository holds a version number. This
  repository runs this model from the
  [cutover](#cutover-from-the-version-file-model) on, and the rest of this
  section describes it.
- **`version_change`**: a committed version file is the version, and a
  release is due when it changes. It is still supported for other
  adopters, and it is how this repository released up to 1.3.0 (see
  [The version-file trigger](#the-version-file-trigger-version_change)).

For this repository, tags are `v{version}` (plain `MAJOR.MINOR.PATCH`,
no pre-releases), and a release is a GitHub Release carrying the wheel
and `SHA256SUMS`. `pyproject.toml` declares `dynamic = ["version"]`: a
release build takes its version from the tag it is built for (see
[Development](development.md#building-from-a-checkout)).

### The pull request title decides the release

Pull requests are squash-merged, so each lands on `main` as one commit
whose subject is the pull request's title, followed by GitHub's
` (#<number>)`. The title must be a Conventional Commit,
`type(scope)!: description`: a lowercase type, an optional lowercase
scope in parentheses, an optional `!`, then `: ` and a description.

The policy's `release.change_types` table maps each type to `minor`,
`patch` or `none`. This repository's table:

| Type | Release |
|---|---|
| `feat` | minor |
| `fix`, `perf`, `refactor`, `revert`, `build`, `style` | patch |
| `docs`, `chore`, `ci`, `test` | none |

- A `!` (`feat!: ...`, `fix(cli)!: ...`) makes a **major** release. It
  is the only way to one: `major` is not a table value, and a
  `BREAKING CHANGE:` footer is not read.
- A `!` on a type that releases nothing (`docs!: ...`) is refused: a
  breaking change must release, so use `feat!` or `fix!`.
- A type the table does not name is refused, and the refusal lists the
  allowed types.

The `PR title` check applies exactly these rules to every pull request
(see [Continuous integration](#continuous-integration)), with the same
code the release uses. Do not edit the squash commit message in GitHub's
merge dialog: the title is what the release reads. A milestone's title
comes from its plan (see
[Milestone branches and pull requests](milestone-branches.md#squash-merges-and-the-pull-request-title)).

### How a commit on `main` is classified

Every push to `main` runs `main.yml`. Its `release-plan` job runs
`tools/release.py classify --commit <sha>` (a full 40-hex SHA of a
commit already on `origin/main`), which prints one state. For a commit
`C`:

1. **The base** is the highest release tag that is an ancestor of `C`.
   With none, the base version is `0.0.0`.
2. **An unsettled base resumes first.** If the base tag has no published
   release (none, or only a draft) and is not in `abandoned_tags`, the
   version is the base's, and `C` classifies `RESUME` at the base tag's
   own commit, whatever came after it. The commits since the base are
   released by the next run: the next push to `main`, or `main.yml` run
   from the Actions tab (`workflow_dispatch`).
3. **The range** is `C`'s first-parent history since the base tag (all
   of it without one). Each commit in it contributes:
   - nothing, when the policy committed at that commit is absent or uses
     `version_change` (a *legacy* commit, from before the cutover);
   - otherwise, its subject's release under the `change_types` committed
     at that commit.
4. **The version** is the base with the range's highest release applied:
   `major` gives `X+1.0.0`, `minor` `X.Y+1.0`, `patch` `X.Y.Z+1`, and
   `none` keeps the base version. With no base tag and nothing that
   releases, `C` is `NO_CHANGE` at `0.0.0`: an adopter who opts in before
   the first tag never publishes `v0.0.0` for a `docs:` pull request.

The state then follows from that version, the tags and the releases:

| State | Meaning | Outcome |
|---|---|---|
| `INVALID_SUBJECT` | a commit in the range has a subject the policy cannot classify, and no `bump_overrides` entry settles it | fail, naming every such commit ([below](#an-unclassifiable-subject-invalid_subject)) |
| `NO_CHANGE` | the version's tag is published at an earlier commit: nothing since it releases (only `none` types or legacy commits) | success, nothing published (the ordinary `docs`/`chore`/`ci`/`test` merge) |
| `RELEASE_DUE` | a new, higher version with no tag yet | build, verify, tag, publish |
| `RESUME` | the version's tag exists (here or at an earlier commit) with no release, or only a draft | build and verify **the tag's own commit**, then complete publication |
| `ALREADY_RELEASED` | the tag is at this commit and its published assets are consistent | success, no-op |
| `ABANDONED_VERSION` | the version's tag is acknowledged in `abandoned_tags` (today only `v1.1.0`) | success, nothing published |
| `BASELINE_UNRELEASED` | a lower tag in the history has no published release and is not acknowledged | fail, naming every such tag ([below](#an-unreleased-tag-below-a-new-version-baseline_unreleased)) |
| `INVALID_TRANSITION` | under `version_change`, a new version that is not higher than the highest tag in the history | fail |
| `COLLISION_TAG_ELSEWHERE`, `COLLISION_RELEASE_WITHOUT_TAG`, `RELEASE_MISMATCH`, `ABANDONED_TAG_INCONSISTENT` | the tags, releases and policy contradict one another | fail, for a human |

For `RELEASE_DUE`/`RESUME`, the `build` job checks out exactly the
target commit and runs the policy's build and verify commands (for this
repository, a wheel build with `WORKFLOW_CONTROLLER_RELEASE_TAG` set to
the tag, then `verify-wheel --tag --commit`). It passes the classified
version to them in `RELEASE_VERSION` (`release-plan`'s `version`
output), not as an argument, so a `RESUME` at an older commit still runs
that commit's own tooling unchanged. It then smoke-tests the wheel with
pipx against `workflow-controller $RELEASE_VERSION` and writes
`SHA256SUMS`. `publish` reclassifies with fresh reads, refuses a target
or version that differs from what was built, re-verifies the artifacts,
and only then (for `RELEASE_DUE`) creates the annotated tag at that
commit and pushes it, then publishes the release and verifies the
published assets. The release body is the policy's `notes` template.

The tag is created only after validation and artifact verification, and
it is never moved or deleted. A run interrupted at any point -- before
the tag push, or after it with no release or a half-uploaded draft -- is
completed by the next push to `main`, or by running `main.yml` from the
Actions tab: both classify `RESUME` at the tag's own commit. A draft is
completed from its present assets, which must verify; a draft that does
not is left for a human, never deleted. A failed or bad *published*
release is fixed with a new release: merge a `fix:` pull request.

`build_origin: "release"` in a wheel's `BUILD_INFO.json` is not proof of
origin (see [Runtime identity](runtime.md#runtime-identity)): check the wheel against the release's
`SHA256SUMS`.

### An unclassifiable subject: `INVALID_SUBJECT`

The `PR title` check and squash-only merging make it rare, but a commit
can still reach `main` with a subject the policy cannot classify: not a
Conventional Commit at all (for example, a squash message edited in the
merge dialog), a type the table does not name, or a `!` on a `none`
type. It is never counted as a patch. The run fails `INVALID_SUBJECT`,
naming each such commit and its subject, and nothing is tagged.

Settle it with a pull request that adds the commit to
`release.bump_overrides` in `.workflow-controller/policy.json`, with the
release it should have made (`major`, `minor`, `patch` or `none`):

```json
"bump_overrides": {"0123456789abcdef0123456789abcdef01234567": "patch"}
```

The overrides are read from the policy committed at the classified
commit, so the push of that pull request's own squash commit is the
first run that uses the entry. Like `abandoned_tags`, it is an
acknowledgement committed on `main`, never a guess:

- it settles only a commit that cannot otherwise be classified. An entry
  naming a commit in the range whose subject classifies, or a legacy
  commit, makes classification refuse (remove the entry in a pull
  request). A valid title's type always wins;
- an entry naming a commit outside the range is ignored: it settled an
  earlier release;
- a commit in the range whose committed policy cannot be parsed also
  refuses, and the same kind of entry settles it.

### An unreleased tag below a new version: `BASELINE_UNRELEASED`

A tag without a published release is never skipped over. Suppose
`v1.2.0` was pushed but its publication failed, and a hand-pushed
`v1.2.1` then landed above it. `v1.2.1` is now the base, and every later
run fails `BASELINE_UNRELEASED`, naming `v1.2.0`. Under
`conventional_commit` the version only moves up from the highest tag, so
the one resolution is to **acknowledge** it: add `"v1.2.0"` to the
policy's `release.abandoned_tags` in a pull request. It then counts as
settled, and the run proceeds. (An unsettled *highest* tag is not this
case: it is the base, and it resumes first.)

**Acknowledging a tag settles only that tag.** Every lower unreleased
tag in the history is checked, not just the highest: if `v1.1.5` is
also unreleased, acknowledging `v1.2.0` still leaves the next run
`BASELINE_UNRELEASED`, naming `v1.1.5`, until it too is acknowledged. A
release published by hand, outside the transaction, is outside this
guarantee.

### The version-file trigger: `version_change`

Under `version_change` the policy names a `version_source` (for example
`pyproject.toml`'s static `[project].version`), and `change_types` and
`bump_overrides` are refused. A release is made by raising the version in
a commit that reaches `main`; a merge that leaves it unchanged classifies
`NO_CHANGE`. The states are the same, except `INVALID_SUBJECT`, which
does not occur, and `BASELINE_UNRELEASED` has a second resolution:
**resume** the lower tag by setting the version back to it, which
classifies `RESUME` at its commit, then raise it again. The title is not
release input (`check-title` passes any title), and milestone pull
requests may use either merge method. Only `conventional_commit`, with
milestone branches enabled, requires `merge_method: "squash"`: only a
squash commit carries the title to `main`.

### Checking a release by hand

`classify` can be run locally to see what the next push to `main` would
do. It needs an authenticated `gh`, and it reads the policy committed at
the classified commit (a commit without `.workflow-controller/policy.json`
is refused):

```bash
git fetch --tags origin
python3 tools/release.py classify --commit "$(git rev-parse origin/main)"
```

`--commit` takes a full 40-hex SHA of a commit already on `origin/main`.
If a release job fails, fix the cause and re-run `main.yml` from the
Actions tab, or let the next push to `main` do it. Never re-push or move
a tag.

A release commit that lands on `main` while a milestone is in flight
moves `main` under that milestone: the milestone then ends at
`integration_required` (see
[Milestone branches and pull requests](milestone-branches.md)), which is
expected under Workflow 2.5.1 and 2.6.0 alike, until a Workflow release
provides a base-moving transition
([ADR 0006](../adr/0006-workflow-release-admission-and-per-release-contracts.md)).

## Repository settings

The GitHub settings this repository runs with. They are not read by any
code; the Controller's own checks (readiness, close-out) do not depend on
them, but the release and review flow assumes them. This is the
configuration from the [cutover](#cutover-from-the-version-file-model) on.

- **`main` is protected** by the ruleset "Main Protection", which applies
  to the default branch with no bypass:
  - changes reach `main` only through a pull request (no approving review
    is required, but every review conversation must be resolved);
  - `main` cannot be deleted or force-pushed, and its history must be
    linear ("Require linear history");
  - the only allowed merge method is **squash**;
  - the required checks are `validate / plan`, `validate / tests-result`,
    `validate / package`, `workflow-conformance` and `PR title`. The
    individual `validate / tests (<i>)` shards are deliberately not
    required: their number changes with the plan, and `tests-result`
    fails whenever a shard fails or is missing;
  - a pull request does **not** have to be up to date with `main` to be
    merged. That requirement would block the `integration_required`
    procedure (merging a milestone pull request that `main` moved ahead
    of) under Workflow 2.5.1 and 2.6.0. Milestone pull requests keep their
    freshness check anyway: the Controller marks one ready only when `main`
    is an ancestor of it.
- **Merge methods** (Settings, General, Pull Requests): squash merging
  only; merge commits and rebase merging are off. The default squash
  commit message is **"Pull request title and description"**, so the
  squash commit's subject is the title followed by ` (#<number>)`, and its
  body is the pull request's description. Close-out recognises a milestone
  squash by exactly that subject (see
  [Milestone branches and pull requests](milestone-branches.md#squash-merges-and-the-pull-request-title)).
- **Auto-merge** is allowed, and **head branches are deleted** after a
  merge. The Controller itself never merges.
- **Do not press "Update branch"** on a milestone pull request. It
  pushes a merge commit the next Controller step refuses (see
  [Milestone branches and pull requests](milestone-branches.md)).
- **Immutable releases** (Settings, General) is recommended: it makes a
  published release's assets and tag unchangeable, even by an admin, and
  a workflow cannot turn it on for itself. It is currently off.

## Cutover from the version-file model

Controller 1.4.0 ships both triggers, but a repository moves to the new
one only when its committed policy says so. The driving Controller reads
the policy at every lifecycle step, and 1.3.0 refuses the new policy,
so the switch is its own small pull request, made between
milestones. For this repository:

1. The milestone that brought the code
   (`workflow-controller-squash-merge-tag-versioning`) is merged with
   "Create a merge commit", as before, and closed out by 1.3.0. Its merge
   still runs the legacy policy with a static `1.3.0` and classifies
   `NO_CHANGE`. `PR title` already runs, and passes ("not release input").
2. The settings change, with the user's approval, and with no pull
   request in flight: squash merging only, the "Pull request title and
   description" default, the ruleset's merge method squash, `PR title`
   required, and linear history required (see
   [Repository settings](#repository-settings)).
3. The cutover pull request changes exactly two files:
   `pyproject.toml`'s `version = "1.3.0"` becomes `dynamic = ["version"]`,
   and `.workflow-controller/policy.json` gains
   `"trigger": "conventional_commit"`, `change_types` and
   `"merge_method": "squash"`, and loses `version_source`. Its title is
   `feat: squash merges with release versions derived from pull request
   titles`, and it is squash-merged.
4. That push classifies `RELEASE_DUE` 1.4.0: the base is `v1.3.0`, every
   commit since it but the cutover is legacy, and the cutover is a
   `feat`.
5. Install 1.4.0 once `status` shows `active: none`. Until then 1.3.0
   refuses every lifecycle command on the repository: it cannot read the
   new policy, and names the unknown key
   `milestone_branches.pull_request.merge_method`. From then on the
   bootstrap starts the next milestone as `/milestone-plan <main tip>`.

To go back, either run 1.4.0 or later, or revert the cutover pull request
(a `revert:` squash). A revert restores the legacy policy and the static
`1.3.0`, which 1.3.0 reads again; the tags stay as they are. Switch the
merge settings back too: in merge mode a squash-merged milestone gates
`merge_method_rewrote_history` instead of closing out.
