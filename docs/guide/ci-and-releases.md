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

The three workflow files are generated. Edit the model in
`tools/ci_workflows.py`, then run `python3 tools/ci_workflows.py --write`;
`python3 tools/ci_workflows.py --check` (and a test) fails when a
committed file differs from the model.

The checks are `validate / plan`, `validate / tests (<i>)`,
`validate / tests-result` and `validate / package`. See
[Repository settings](#repository-settings) for which of them `main`
requires.

## Releasing

A release is **driven by a version change on `main`**. The repository
policy's `release` section names the version source, the tag format,
the build and verify commands, the artifacts and the publication target;
for this repository that is `pyproject.toml`'s static
`[project].version` (plain `MAJOR.MINOR.PATCH`, no pre-releases), tags
`v{version}`, and a GitHub Release carrying the wheel and `SHA256SUMS`.
The generic transaction is `controller/release_txn.py`;
`tools/release.py` is this repository's thin CLI over it.

For maintainers:

1. Bump the version in `pyproject.toml` in a commit that reaches `main`
   through a pull request. `main` is protected, so there is no other way
   (see [Repository settings](#repository-settings)). A milestone that
   changes the shipped package should bump the version itself: a merge
   that leaves it unchanged classifies `NO_CHANGE` and publishes nothing,
   which is how 1.2.1 came to need a separate bump-only pull request.
2. The push to `main` runs `main.yml`. `release-plan` runs
   `tools/release.py classify --commit <sha>`, which compares the
   version with the release tags in the commit's history and prints one
   state (`--commit` takes a full 40-hex SHA of a commit already on
   `origin/main`):

   | State | Meaning | Outcome |
   |---|---|---|
   | `NO_CHANGE` | the version's tag is at an earlier commit and released | success, nothing published (the ordinary merge) |
   | `RELEASE_DUE` | a new, higher version with no tag yet | build, verify, tag, publish |
   | `RESUME` | the version's tag exists (here or at an earlier commit) with no release, or only a draft | build and verify **the tag's own commit**, then complete publication |
   | `ALREADY_RELEASED` | the tag is at this commit and its published assets are consistent | success, no-op |
   | `ABANDONED_VERSION` | the version's tag is acknowledged in `abandoned_tags` (today only `v1.1.0`) | success, nothing published |
   | `BASELINE_UNRELEASED` | a lower tag in the history has no published release and is not acknowledged | fail, naming every such tag (resolutions below) |
   | `INVALID_TRANSITION` | a new version that is not higher than the highest tag in the history | fail |
   | `COLLISION_TAG_ELSEWHERE`, `COLLISION_RELEASE_WITHOUT_TAG`, `RELEASE_MISMATCH`, `ABANDONED_TAG_INCONSISTENT` | the tags, releases and policy contradict one another | fail, for a human |

3. For `RELEASE_DUE`/`RESUME`, `build` checks out exactly the target
   commit, runs the policy's build and verify commands (for this
   repository `verify-wheel --tag --commit`), smoke-tests the wheel with
   pipx and writes `SHA256SUMS`. `publish` reclassifies with fresh reads,
   refuses a target that differs from what was built, re-verifies the
   artifacts, and only then (for `RELEASE_DUE`) creates the annotated tag
   at that commit and pushes it, then publishes the release and verifies
   the published assets.

The tag is created only after validation and artifact verification, and
it is never moved or deleted. A run interrupted at any point -- before
the tag push, or after it with no release or a half-uploaded draft -- is
completed by the next push to `main` that still carries the version, or
by running `main.yml` from the Actions tab (`workflow_dispatch`): both
classify `RESUME` at the tag's own commit. A draft is completed from
its present assets, which must verify; a draft that does not is left
for a human, never deleted. A failed or bad *published* release is fixed
with a new PATCH version.

`build_origin: "release"` in a wheel's `BUILD_INFO.json` is not proof of
origin (see [Runtime identity](runtime.md#runtime-identity)): check the wheel against the release's
`SHA256SUMS`.

### An unreleased tag below a new version: `BASELINE_UNRELEASED`

A tag without a published release is never skipped over. Suppose
`v1.2.0` was pushed but its publication failed, and `main` then moved to
1.2.1 (by a bump, or by a hand-pushed `v1.2.1`). Every later run fails
`BASELINE_UNRELEASED`, naming `v1.2.0`, until one of two commits on
`main` resolves it:

- **resume** it: set the version back to `1.2.0`. That commit classifies
  `RESUME` at `v1.2.0`'s own commit and publishes it; the bump to 1.2.1
  is then `RELEASE_DUE` again;
- **acknowledge** it: add `"v1.2.0"` to the policy's
  `release.abandoned_tags`. It then counts as settled, and the run
  proceeds with 1.2.1.

**Acknowledging a tag settles only that tag.** Every lower unreleased
tag in the history is checked, not just the highest: if `v1.1.5` is
also unreleased, acknowledging `v1.2.0` still leaves the next run
`BASELINE_UNRELEASED`, naming `v1.1.5`, until it too is resumed or
acknowledged. A release published by hand, outside the transaction, is
outside this guarantee.

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
expected.

## Repository settings

The GitHub settings this repository runs with. They are not read by any
code; the Controller's own checks (readiness, close-out) do not depend on
them, but the release and review flow assumes them.

- **`main` is protected** by the ruleset "Main Protection", which applies
  to the default branch with no bypass:
  - changes reach `main` only through a pull request (no approving review
    is required, but every review conversation must be resolved);
  - `main` cannot be deleted or force-pushed;
  - the only allowed merge method is **merge commit**;
  - the required checks are `validate / plan`, `validate / tests-result`,
    `validate / package` and `workflow-conformance`, and the branch must
    be up to date with `main` before merging. The individual
    `validate / tests (<i>)` shards are deliberately not required: their
    number changes with the plan, and `tests-result` fails whenever a
    shard fails or is missing.
- **Merge methods**: merge commits only; squash and rebase merging are off.
  Squash and rebase merges take the reviewed Workflow commits off `main`.
  Close-out detects them (`merge_method_rewrote_history`) but cannot undo
  them. Moving to squash merges with versions derived from pull request
  titles is planned; it needs a Controller release first (see
  [the roadmap](../ROADMAP.md)).
- **Auto-merge** is allowed, and **head branches are deleted** after a
  merge. The Controller itself never merges.
- **Do not press "Update branch"** on a milestone pull request. GitHub
  may offer it, because `main` requires an up-to-date branch, but it
  pushes a merge commit the next Controller step refuses (see
  [Milestone branches and pull requests](milestone-branches.md)).
- **Known conflict:** the "up to date with `main`" requirement blocks the
  documented `integration_required` procedure, which merges a milestone
  pull request without integrating `main` first. While the requirement
  is on, a milestone whose `main` moved cannot be merged that way. Either
  relax the requirement (the Controller's readiness check already refuses
  a branch `main` is not an ancestor of) or decide the procedure before
  the next milestone that meets `integration_required`.
- **Immutable releases** (Settings, General) is recommended: it makes a
  published release's assets and tag unchangeable, even by an admin, and
  a workflow cannot turn it on for itself. It is currently off.
