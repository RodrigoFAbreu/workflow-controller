# Continuous integration and releases

> For: maintainers of a repository that releases through the Controller, and of this one. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

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
  record and log as the `results-<i>-attempt-<a>` artifact, where `<a>`
  is the run attempt (`github.run_attempt`), also written into the
  record. A shard that does not pass, or that leaked a process, fails its
  job and prints its failing tests with their traceback tails, or the
  leaked processes, in its own step log;
- `tests-result`: runs even when a shard failed or never started, and
  aggregates every shard's result (see
  [Re-running failed jobs](#re-running-failed-jobs)). It passes only if
  every planned test ran exactly once and passed and no shard leaked a
  process; a missing plan or a missing shard result fails it. It uploads
  the plan and the records it counted as the `timings-ci-attempt-<a>`
  artifact. `tools/test_shards.py`'s CI placement keeps `tests.test_packaged_runtime`
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
the artifacts to download (`results-<i>-attempt-<a>` holds the log,
`test-plan` the `plan.json` the replay command reads) and the
reproduction commands.

### Re-running failed jobs

GitHub's "Re-run failed jobs" counts. Each attempt uploads its own
`results-<i>-attempt-<a>` artifacts, and `tests-result` downloads every
attempt's, one directory each. For each planned shard it takes the
record with the highest attempt, and only those records decide the
result. A shard the re-run did not schedule keeps its earlier record. So
after a flaky shard fails, re-running the failed jobs re-runs that shard
(and `tests-result`), and a green re-run turns `tests-result` green.

Nothing is hidden. The summary gives each shard's attempt, and a
"Superseded attempts" section lists every record it did not count, with
its attempt, its verdict and its failing tests. `tests-result` also
fails (exit 2), naming the job, when this attempt's `plan` or `tests`
job did not succeed but the records it found would pass: a job scheduled
in this attempt failed without leaving a fresh record or plan, and an
earlier attempt's passing record must not stand in for it. The same exit
refuses two records of one shard and attempt, a record from a later
attempt than the aggregating one, and an artifact directory whose name
disagrees with its record.

**A leaked process fails the run.** A test that leaves a process running
after its shard ends gets the verdict `LEAKED`: the process is reported
and killed, and the shard's job and `tests-result` are red, even if
every test passed. The summary's "Leaked processes (failure, killed)"
section names each process, with its shard, pid, age and command line.
Since the shard's own job is red, "Re-run failed jobs" re-runs it, as for
a failed test.

**Nothing is retried automatically.** A failed test is reported failed,
and never run a second time by the runner or the workflows. A retry
would turn an intermittent defect, in a test or in the Controller, into
a green run (one of the flakes 1.4.2 fixed was a Controller defect).
The only re-run is the one a person asks for, and its summary still
shows what it superseded.

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
published assets. The release body is the policy's `notes` template,
which is also the annotated tag's message; a template that uses
`{release_notes}` fills it from the milestones (see
[Release notes from the milestones](#release-notes-from-the-milestones)).
Tags are created with `--cleanup=verbatim`, so a `#`-leading line in the
message survives. A message with no such line and no trailing
whitespace, like this repository's, is stored exactly as before.

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

### Release notes from the milestones

The policy's `release.publication.notes` template may use the
`{release_notes}` placeholder, which is allowed there only. A repository
opts in with it and with `milestone_branches.pull_request.release_notes`,
which makes readiness put each milestone's notes block in its pull
request body (see
[Milestone branches and pull requests](milestone-branches.md#release-notes-in-the-pull-request-body)).
A template without the placeholder renders exactly as before, and
nothing in this section applies to it.

**Where the text comes from.** Only from the notes blocks in the squash
commit messages of the release range, verified against their digests.
The range is the one classification walks: every first-parent commit
since the base tag, not only the commit being released. A `docs:` merge
that lands while a milestone's run waits replaces that run (the
`main-release` concurrency group), and its release still finds the
milestone's block in the range. The release reads no milestone
narrative, no file of any commit's tree and no forge data. Each block's
notes must hash to its marker's `sha256=`. One block gives its notes as
they stand; several are each preceded by a `### <work_item_id>` line and
separated by blank lines, in commit order. The rendered template, with
trailing whitespace stripped, is both the tag message and the release
notes. A repository with no release tag yet publishes every block its
history holds, each under its work-item id.

**The squash commit must carry the pull request body.** That needs
GitHub's default squash merge commit message "Pull request title and
description" (see [Repository settings](#repository-settings)), and no
edit in the merge dialog. GitHub then rewraps the body in a way that was
measured, not documented: a line longer than 72 characters is broken
greedily, at spaces only; every existing line break is kept, and lines
are never joined; a line of 72 characters or fewer is left alone. That
is why readiness refuses a notes line over 72 bytes: shorter lines reach
the commit byte for byte. A marker line over 72 characters is broken at
a space, and the release reads it with every run of whitespace as one
space. If GitHub ever wraps differently, the digest no longer matches
and the release refuses. It never publishes altered notes.

**Included or refused.** There is no third outcome: an opted-in release
never publishes empty notes. The publish prints the outcome
(`ok: release notes: included <id> (<commit>)`, with any superseded
blocks). The notes are **included** when at least one block is used, and
every used block is well formed, holds a non-blank line and matches its
digest. Anything else is **refused** before the tag is created, so
nothing is tagged or published:

- **missing**: no commit of the range carries a block. A milestone with
  no notes section, one bound before the opt-in, one merged at
  `release_notes_invalid`, a squash commit that lost its body, and a
  release with no milestone in its range all end here;
- **unreadable**: a Git read failed (rerun the publish), or a message
  that holds a marker line is not valid UTF-8;
- **unverified**: a used block is damaged (a malformed start marker, no
  end marker, empty notes, or two blocks for one work item in one
  message), its digest does not match, or a marker line names no work
  item.

An empty notes block is never included, even when its digest is the
SHA-256 of empty text.

**A newer block supersedes an older one.** For each work item the block
of the newest commit of the range that carries one is used. An older
block of the same work item is superseded, whatever it holds, and the
output names it with its commit.

**The fix: supply the notes in a later trunk commit.** The range's
commits cannot change, so a later commit on `main` carries a block for
each work item whose notes are missing or failed. The publish then
releases that commit: no tag was created, so the range grows to include
it. The block is either:

- the merged pull request's block, copied verbatim from its body on
  GitHub, which keeps it as readiness wrote it; or
- your own notes, rendered by
  `python3 tools/release.py notes-block --work-item <id> <file>`. It
  trims outer blank lines, applies readiness's checks, and prints the
  block with its digest, or refuses naming the failed check (an empty
  file included). A release with no milestone in its range takes a
  work-item id of your choosing.

In this repository that commit is the squash merge of a `docs:` pull
request whose body is the block, or several blocks. A `docs:` title
releases nothing by itself, and its merge becomes the release commit of
the pending release. A commit made with `git commit` instead of a squash
merge needs `--cleanup=verbatim` (for example
`git commit --cleanup=verbatim -F message.txt`): Git's default cleanup
strips `#`-leading lines, such as a `### ` heading in the notes, which
changes the digest.

**The opt-out.** Every refusal also names the policy's explicit opt-out:
a trunk commit that removes `{release_notes}` from
`release.publication.notes`. The publish reads the policy committed at
its release commit, so it then renders the fixed text. Two refusals name
no work item a block could supersede, so the opt-out is their only fix:
a marker line that names no work item, and a message that holds a
marker line and is not valid UTF-8. Every refusal clears for the release
that hit it, and none carries into later releases: the next range starts
at the tag this one creates.

**Quoting the marker.** Only marker lines are read: lines that start
with `<!-- workflow-controller: release-notes`. A pull request or
commit body may quote the marker mid-line, in backticks for example,
and that text is ignored. It must never start a line with it outside
readiness's block. A message with no marker line never refuses, whatever
its encoding.

**What is not detected.** A squash commit that lost the pull request
body (a message edited in the merge dialog, or another squash message
setting) is caught only when no other block is in the range, as
**missing**. When another milestone's block is in the range, the release
publishes that block without the lost milestone's notes. Check that the
publish output names every milestone you expect.

**Milestones bound before the opt-in.** Readiness takes the notes
location from the binding's policy snapshot, so a milestone bound before
the repository opted in carries no marker. Its release refuses as
**missing**, unless another block is in the range, until you supply its
notes.

**Resuming a release.** On `RESUME` with no release yet, the publish
reuses the tag's message as the notes, but only after it recomputes the
notes over the tag's own range (from the highest lower release tag that
is an ancestor) and renders the current template with them. The tag's
message must be equal byte for byte. Any difference refuses as an
**unverified tag**: notably a tag created before the opt-in, which holds
the old fixed text, but also a tag made under another template or from
a range whose blocks changed. A tag that is not annotated, or whose
message is not valid UTF-8, also refuses; a failed read clears on a
rerun. When another run pushes the same tag at the same commit just
before this run's push, this run checks the winning tag the same way,
reading its message from the remote rather than from its own local tag.
The publish never edits or re-creates a pushed tag. The fix is to
create the release for that tag by hand, with the right notes. Create it
as a draft: the next run then uploads what the draft lacks and publishes
it, keeping the draft's notes, which are yours.

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

With auto-merge on, a milestone whose release did not publish stops at
`release_failed` before close-out (see
[Auto-merge and the release wait](milestone-branches.md#auto-merge-and-the-release-wait)).
The gate names each run of the publishing workflow that did not succeed,
with its URL, or says that the workflow succeeded and published nothing
(nothing to re-run then: publish by hand, or let a later push to `main`
publish a covering release). Resolve it the same way: "Re-run failed jobs" on the named
run (the transaction resumes, `RESUME`), or publish by hand with
`tools/release.py`. The next Controller step classifies the squash
commit again; once the release is published, or a later trunk run has
published a release that covers it, the milestone closes out. The
Controller itself never re-runs, tags or publishes anything.

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
  [Milestone branches and pull requests](milestone-branches.md#squash-merges-and-the-pull-request-title)),
  and the release reads the milestone's notes block from that body once
  the repository opts in (see
  [Release notes from the milestones](#release-notes-from-the-milestones)).
- **Auto-merge** is allowed, and **head branches are deleted** after a
  merge. The Controller never uses GitHub's auto-merge request. When a
  repository's policy opts in with
  `milestone_branches.pull_request.auto_merge` (from 1.6.0; see
  [Auto-merge and the release wait](milestone-branches.md#auto-merge-and-the-release-wait)),
  the Controller itself squash-merges an accepted milestone pull request,
  through one `gh pr merge --squash --match-head-commit <acceptance
  commit>` per attempt, under the ruleset and its required checks, and
  only after it has seen every check green. That needs squash merging
  allowed, which it is here; the merge runs as the account `gh` is
  authenticated as. The policy's `release_workflow` (default `main.yml`)
  names the workflow whose trunk run publishes the release the Controller
  then waits for. A repository that requires a **merge queue** is not
  supported: the queue writes its own squash message, and the merge then
  fails verification (`MERGED_REWRITTEN`). This repository's policy does
  not opt in yet; the opt-in is a `chore:` pull request after 1.6.0 is
  installed.
- **Do not press "Update branch"** on a milestone pull request. It
  pushes a merge commit the next Controller step refuses (see
  [Milestone branches and pull requests](milestone-branches.md)).
- **Immutable releases** (Settings, General) is recommended: it makes a
  published release's assets and tag unchangeable, even by an admin, and
  a workflow cannot turn it on for itself. It is currently off.

## Cutover from the version-file model

A repository moves from the version-file trigger to the
`conventional_commit` trigger only when its committed policy says so. The
driving Controller reads the policy at every lifecycle step, and a
Controller before 1.4.0 refuses the new policy (it names the unknown key
`milestone_branches.pull_request.merge_method`), so the switch is its own
small pull request, made between milestones, with no work item in flight:

1. Change the repository settings first: squash merging only, the "Pull
   request title and description" default, the ruleset's merge method
   squash, `PR title` required, and linear history required (see
   [Repository settings](#repository-settings)).
2. The cutover pull request changes two files: `pyproject.toml`'s static
   `version = "..."` becomes `dynamic = ["version"]`, and
   `.workflow-controller/policy.json` gains `"trigger":
   "conventional_commit"`, `change_types` and `"merge_method": "squash"`,
   and loses `version_source`. Its title is a Conventional Commit that
   releases, and it is squash-merged.
3. Install a Controller of 1.4.0 or later once `status` shows `active:
   none`. From then on the bootstrap starts the next milestone as
   `/milestone-plan <main tip>`.

To go back, run a Controller of 1.4.0 or later, or revert the cutover pull
request (a `revert:` squash), which restores the legacy policy and the
static version. Switch the merge settings back too: in merge mode a
squash-merged milestone gates `merge_method_rewrote_history` instead of
closing out.
