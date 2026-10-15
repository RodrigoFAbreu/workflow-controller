# Continuous integration and releases

> For: maintainers of a repository that releases through the Controller, and of this one. Last checked with: Controller 1.9.0; Workflow 2.10.0.

[Back to the documentation map](../README.md)

## Continuous integration

`.github/workflows/validate.yml` is the single definition of required
validation. It is called, never triggered directly, and has four jobs,
each on `ubuntu-latest` with Python 3.12 and read-only permissions
(`tests-result` also has `actions: read`, which only the reuse path below
uses):

- `plan`: plans the whole CI selection with
  `tools/run_tests.py plan --profile ci --ci-placement`: every test
  `python3 -m unittest discover -s tests -t .` loads, plus the nine frozen
  Workflow conformance suites, in duration-balanced shards, from the
  committed `tools/test_timings.json`. Its outputs are the shard indexes,
  their count and the plan's digest, and it uploads `plan.json`;
- `tests`: one matrix job per planned shard (`fail-fast: false`). Each
  recomputes the plan from the checked-out commit, refuses to run unless
  its digest equals `plan`'s, runs its shard (its Controller tests and,
  beside them in a second process, its Workflow conformance suites; see
  [How a shard runs](#how-a-shard-runs)) and uploads its result
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
untouched. It still runs the nine conformance suites in one job, on every
pull request (10 to 14 minutes in recent runs with Workflow 2.10.0).
`validate` runs the same suites through the planner as well; the
acceptance-matrix suite (8 to 12 minutes on CI) is its own largest atom.
Both are now close to the floor on when all of a pull request's checks
finish, so the wall time of a shard counts.
`release.yml` is gone: pushing a `v*` tag by hand triggers nothing.

The CI plan uses the `ci` profile: a target of 180 s of work per shard,
at least 2 and at most 4 shards, from the committed `tools/test_timings.json`
only. The maximum wins today, so CI runs four shards. The maximum is a
runner budget: 4 shards, `package` and the managed `workflow-conformance`
job hold at most 6 Linux runners for one pull request, on an account that
shares 20 jobs. Refresh `tools/test_timings.json` (see the development
guide) when the longest shard passes about 14 minutes; it was refreshed on
2026-10-13 and drifts within days as the acceptance matrix and the
scenario tests grow. A stale or missing entry costs wall time
(the shard count and the balance), never coverage. The plan is
deterministic: each `tests` job recomputes it and refuses to run on a
digest mismatch, so the matrix carries only shard indexes. Every
`tests-result` summary states the coverage check's verdict. A failing
run's summary also names each failing test, its shard, the shard's log,
the artifacts to download (`results-<i>-attempt-<a>` holds the log,
`test-plan` the `plan.json` the replay command reads) and the
reproduction commands.

### How a shard runs

A shard has two lanes that run at the same time on its runner. The
Controller lane runs the shard's Controller tests through `unittest`, as
before. The conformance lane runs the shard's frozen Workflow suites, one
at a time in plan order, each as `cd scripts && python3 <file>`, in a helper
process (`python3 tools/test_shards.py --conformance-lane`) that the shard
starts before its first test. A shard therefore lasts as long as its longer
lane, and the planner balances that: the conformance suites are placed
first, longest first, and each Controller atom then goes where the shard's
estimate (the longer lane, times 1.15 when both lanes have work) ends up
smallest. The plan records `conformance_lane: true`, so its digest tells
the modes apart.

Nothing else changes: the same per-atom results, the exact-coverage proof,
the leak scan (after both lanes end) and the exit status. A suite's output
is appended to `shard-<i>.log` as one section after the `unittest` output,
so a conformance failure is found after the Controller tests in the log. If
the helper process dies, the shard fails with exit 2; the shard's step log
says the executor refused it, and the `REFUSED` lines in its log name the
conformance lane. The lane files are named per run (`lane-<shard>-<run id>.*` in the results directory) and a shard removes its earlier ones first, so an aborted run's leftovers are never accepted. If the helper dies at any point before the executor ends the lane, even after the suites finished, the shard fails ("conformance lane crashed") and the group's survivors are listed as leaked processes; the guard notices at once, whether or not the executor is still polling, and the executor watches the guard with a pidfd, so an unreaped (zombie) process is seen as dead. The lane is started through a launcher that exits at once, so none of its processes is a child of the shard process (Controller tests there kill and reap children they did not start). A small guard process leads the lane's session, is its child subreaper and starts the helper that runs the suites. The helper stays alive after its suites finish, and so does the guard, until the executor has run its leak scan and ends the lane; a suite's surviving child is adopted by the guard, not by a Controller test in the shard process. The lane ends with the executor by every path: the guard watches a pipe from the executor, so a signal, a crash or `os._exit` in a test ends the guard, the helper and the suite it is running too. A suite's survivors are reported as leaked processes before they are cleaned up, the same as in serial mode (the shard fails). SIGINT and SIGTERM to the executor also end the lane's whole process group, descendants included. Before it deletes the lane files, the executor reads any crash record the guard left, so a helper that died after the suites finished is still reported even if the executor's monitor had not noticed yet.

Known residuals, both accepted. A SIGKILL of the executor cannot run any cleanup in the executor, but the guard holds a pipe from it and ends the whole lane session when the pipe closes, so only a descendant that left the session (`setsid`) can survive. A SIGKILL of the lane's own guard by an outside actor, immediately followed by a SIGINT to the executor, can leave suite descendants running; it is a double fault, and the executor's monitor normally ends the group in between. In both cases GitHub tears the job down, and a local run's supervisor marker scan reports the leftovers, the same as the serial design.

Expected with the 2026-10-13 profile: the shard holding the
acceptance matrix runs only that suite (about 11 minutes), the other three
run conformance lanes of about 5.2, 3.4 and 3.0 minutes beside about 11 minutes of
Controller tests, so the longest shard is about 12.4 minutes and a pull
request's checks finish in about 14. A local run (`tools/run_tests.py`
without `exec-shard`) uses the same executor and so the same two lanes,
except `--serial`, which plans `conformance_lane: false`: the suites then
run one at a time after the Controller tests, as the serial reference.

Measured on the run of PR #46, which added the conformance lane:

| | Before PR #46 | After |
|---|---|---|
| A pull request's checks finish after | 17.7 to 19.3 minutes | about 14.0 minutes (the `CI` workflow alone 12.7) |
| Peak Linux runners | 6 | 6 |
| Runner-minutes | about 69.5 | about 63.0 |

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

The workflow files `ci.yml`, `main.yml`, `main-reuse-report.yml`, `validate.yml` and
`pr-title.yml` are generated. Edit the model in
`tools/ci_workflows.py`, then run `python3 tools/ci_workflows.py --write`;
`python3 tools/ci_workflows.py --check` (and a test) fails when a
committed file differs from the model.

The checks are `validate / plan`, `validate / tests (<i>)`,
`validate / tests-result`, `validate / package` and `PR title`. See
[Repository settings](#repository-settings) for which of them `main`
requires.

### Reusing a pull request's results on `main` (`CI_MAIN_REUSE`)

A squash merge makes a new commit, but its **tree** (the files) equals the
tree of the pull request's merge ref whenever `main` did not move after the
pull request's last CI run. For that tree the whole validation already ran.
`main.yml` can therefore skip the shards and re-check the recorded results
instead of running the same tests a second time. Whether it does is set by
one repository variable, `CI_MAIN_REUSE` (Settings, Secrets and variables,
Actions, Variables):

| Value | What `Main` does |
| --- | --- |
| unset, empty or `report` | **Report only (the default).** The separate workflow `main-reuse-report.yml` (job `reuse-report`) works out what it would do and writes it to its job summary. It is not part of `Main`: nothing in `Main` waits for it, it holds no `main-release` concurrency slot and it cannot fail `Main`, so `Main` is exactly as fast as without it. The full validation runs as before. |
| `on` | **Reuse.** The `reuse` job runs first (about a minute, which `validate` waits for). When every check passes, `validate` does not run the shards: `tests-result` verifies the recorded tree and plan digest and re-aggregates the recorded records. Any failed check runs the full shards. |
| `off` | Neither job runs; nothing is looked up and nothing changes. |
| anything else | Treated as `off` (nothing is looked up), and the `reuse-report` summary says so. Values are compared without regard to case (`ON` is `on`) and are not trimmed (`" on "` is an unknown value). |

**What the jobs check** (`reuse` in `main.yml` and `reuse-report` in `main-reuse-report.yml` share the logic in `tools/ci_reuse.py`; permissions `actions: read`,
`contents: read`, `pull-requests: read`; it cannot write). It finds the pull
request that was merged as this commit, requires that it came from this
repository, and takes its latest successful `CI` run on the pull request's
final head. It downloads that run's `timings-ci-attempt-<a>` artifact (the
highest attempt) and computes the plan for this commit. It reuses only if the
artifact's recorded tree equals this commit's tree **and** the recorded plan
digest equals the plan computed here. It never reuses for `workflow_dispatch`,
a fork pull request, a moved base, no successful run, a run
from before reuse existed (its artifact has no `tree.txt`), or any API
error. A reused run still goes through `tests-result`, which verifies the
tree and digest again and fails the run (no release) on a mismatch, and
re-runs `aggregate` on the records: each planned test exactly once, all
passed, no leaked process. `release-plan`, `build` and `publish` keep
`needs: validate`, and each states in its own `if` that every job it needs
must be `success` (a skipped `reuse` upstream, as in report and `off` modes,
must not suppress a release; `!cancelled()`, never `always()`).

**Reading the report.** Open the `Main reuse report` run for the push (the `reuse` job's summary in the `Main` run, once the variable is `on`).
It lists this commit's tree, the merged pull request and its head, the
candidate `CI` run id, the recorded tree and plan digest next to this
commit's (each marked `equal` or `DIFFERENT`), and a last line: `Decision:
would reuse.` or `Decision: would not reuse: <reason>.` To check a decision
against reality, open the candidate run (it is the id in the summary) and
compare: if the report said "would reuse", the full `validate` of this `Main`
run should be green, and its test count equal to the pull request's. A
"would reuse" next to a red `validate` is a bug in the decision. Do three or
more merges before switching to `on`. By 2026-10-15 three cross-checks were made
(PRs #47, #48 and #45) and all three matched; switching to `on` is the owner's decision.

**Switching.** `gh variable set CI_MAIN_REUSE --body on` turns reuse on and
`gh variable set CI_MAIN_REUSE --body report` goes back to report only;
`gh variable delete CI_MAIN_REUSE` is the same as `report`, and
`--body off` removes the job's work as well. It is a variable change, not a
code change, and takes effect on the next push to `main`.

**Rolling back.** If a reused `Main` run is wrong, set the variable to `off`
(or `report`) and choose **Re-run all jobs**: the full shards run. `validate`
is handed a run id only while the variable is `on` when it is evaluated, so
a retry after the change does not reuse. **Re-run failed jobs** is not a
rollback: it keeps the jobs that already succeeded, `reuse` and its old
output among them, and re-runs only the failed ones, so it is not
guaranteed to run the full shards. Use **Re-run all jobs**.

What it gives up: with `on`, a tree is tested once, on the pull request. The
second execution would only catch a flaky pass, a change of the runner image
between the two runs, or a time-dependent test. See
[ADR 0014](../adr/0014-reuse-pull-request-results-on-main.md).

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
