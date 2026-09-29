# ADR 0007: Tag-derived versions and squash merges

Status: accepted (2026-09-29). See
`docs/ai-workflow/CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md` for the full
design record (work item `workflow-controller-squash-merge-tag-versioning`,
`docs/ROADMAP.md` step C1, section 11.1). The code ships as the first
tag-derived release, 1.4.0, produced by the cutover pull request (below). This
document records the Conventional Commit title and the new policy trigger, the
tag-derived version, release classification from commit types, the pull
request's title and body, squash close-out and the explicit base, the cutover,
and the alternatives rejected. It adds no exit code: the table in
[ADR 0001](0001-controller-generation-1-architecture.md) stays the normative
exit-code contract, unchanged.

It supersedes two statements of
[ADR 0003](0003-trunk-branch-pr-release-orchestration.md), which carries a
pointer here: "`pyproject.toml`'s static `[project].version` is the only
human-maintained version" (under "Version authority"), and "only
history-preserving merges converge cleanly", with its policy that has no
merge-method field. Both still describe a repository whose policy keeps the
`version_change` trigger and no `merge_method`, which behaves exactly as under
1.3.0.

## Context

Through 1.3.0 a release was driven by a hand-edited `pyproject.toml` version
reaching `main`, and a milestone pull request was merged with a merge commit.
A milestone that changed the shipped package had to remember the bump; 1.2.1
needed a separate bump-only pull request because one did not. The user decided
(2026-09-29) to move this repository, and the Controller's generic release and
pull-request machinery, to the model SignalHub already runs: squash merges
only, a Conventional Commit pull request title that becomes the squash
commit's subject, the commit type deciding the release, and the Git tag as the
only version authority. The release safety of ADR 0003 (build and verify
before the tag, immutable tags, `RESUME`, `abandoned_tags`, serialized
releases) was to stay.

Two measured facts shaped the design:

- **Workflow's registry cannot carry a change type.** It is written only by
  Workflow's `generate_registry`, which emits a fixed set of fields. A
  Controller-only change cannot add one there without breaking plan-approval
  coverage.
- **An older Controller refuses a policy value it does not know**, at every
  lifecycle step, because each step reads the committed policy strictly. The
  1.3.0 Controller driving this milestone would therefore have been locked out
  of its own milestone by any policy switch inside it.

## Decisions

### Conventional Commit titles and the `conventional_commit` trigger

- **One parser decides everywhere.** `controller/conventional_commit.py`
  carries SignalHub's grammar unchanged:
  `type(scope)!: description`, a lowercase type, an optional lowercase scope,
  an optional `!`. A ` (#N)` squash suffix is part of the description.
  `tools/release.py check-title`, the release classification and the title
  the Controller sets all use it, against the policy committed at the relevant
  commit.
- **The policy's table decides the bump.** `release.trigger:
  "conventional_commit"` requires `release.change_types`, a map from type to
  `minor`, `patch` or `none`, and forbids `version_source`: the tags are the
  version. `major` is not a table value; only `!` gives a major release, and
  `!` on a type mapped to `none` is refused ("a breaking change must
  release"). An unknown type is refused. This repository's post-cutover
  table: `feat` minor; `fix`, `perf`, `refactor`, `revert`, `build` and
  `style` patch; `docs`, `chore`, `ci` and `test` none. The rule behind it is
  that a type that can change the shipped package releases.
- **`release.bump_overrides` acknowledges, never decides.** It maps a full
  commit id to a bump, committed on `main` like `abandoned_tags`. It settles
  only a trunk commit that cannot otherwise be classified (an unparsable
  subject, an unknown type, `!` on a `none` type, or an unparsable committed
  policy). An entry naming a commit whose subject classifies, or a legacy
  commit, makes classification refuse.
- **`milestone_branches.pull_request.merge_method`** is optional: `merge`
  (the default, 1.3.0's behaviour) or `squash`. With both releases and
  milestone branches enabled, the `conventional_commit` trigger requires
  `squash`, since only a squash commit carries the title to `main`.
- **Every 1.3.0 policy parses unchanged, with the same meaning.** Every new
  key is optional, so a binding that stored 1.3.0-era policy bytes keeps
  working. `schema_version` stays 1.
- **The `PR title` check.** The generated `.github/workflows/pr-title.yml`
  runs `tools/release.py check-title "$TITLE"` on every pull request event
  that can change the title, with the title passed through the environment
  only and read-only permissions. Under `version_change` it passes and says
  the title is not release input.

### The version at build time and at run time

- **The highest reachable release tag is the version.** `controller/version.py`
  derives it: a static `[project].version` wins when `pyproject.toml` declares
  one (every checkout before the cutover); otherwise the version is the highest
  strict `vMAJOR.MINOR.PATCH` tag reachable from the commit, or `0.0.0` when
  none is (an unborn `HEAD`, a shallow clone without tags, a tree outside
  Git). The Controller's own tag format is the fixed `v{version}`, never read
  from an adopter's policy.
- **Build time.** With `dynamic = ["version"]`, `setup.py` gives a release
  build the version of `WORKFLOW_CONTROLLER_RELEASE_TAG` (a malformed tag
  fails the build) and a local build the derived version.
- **Run time.** A source runtime reports the derived version. A pinned source
  snapshot has no `.git`, so its version is derived from the origin checkout
  at the snapshot's commit and recorded in `SOURCE_PIN.json`. A package
  runtime is unchanged: its metadata, cross-checked against `BUILD_INFO.json`.
- **Strict `MAJOR.MINOR.PATCH` stays.** A checkout between two releases
  reports the last release it contains, exactly as a checkout did before the
  cutover while `pyproject.toml` still named that release. A local build is
  told apart by `BUILD_INFO.json`'s `build_origin` and commit, not by the
  version string.

### Release classification from commit types

- **The trigger committed at `C` selects the model.** Under `conventional_commit`
  only the computation of the version at `C` changes; every existing row of
  ADR 0003's table then applies unchanged.
- **The base** is the highest matching tag in `C`'s history (`0.0.0`, with no
  tag, when there is none). **An unsettled base resumes first**: when the base
  tag has no published release (or only a draft) and is not abandoned, the
  version at `C` is the base version, and the rows give `RESUME` at the tag's
  own commit whatever came after it. The next run releases the bump.
- **The range** is `C`'s first-parent history since the base. A commit whose
  committed policy is absent or `version_change` is legacy and contributes no
  bump; its release was decided by its own rule. Every other commit's subject
  is classified against its own committed `change_types`. The highest bump
  wins: `major`, `minor` and `patch` bump the base, `none` keeps it.
- **Fail closed on an unclassifiable subject.** Without an override, such a
  commit classifies `C` as the new failing state `INVALID_SUBJECT`, naming
  the commit, its subject and the fix. Nothing is tagged. A commit whose own
  committed policy cannot be read refuses the same way.
- **No tag and no bump is `NO_CHANGE` at `0.0.0`**, before the rows. Without
  it an adopter who opted in before a first tag would publish an immutable
  `v0.0.0` for a `docs:` pull request.
- **`build` and `verify` take the classified version from `RELEASE_VERSION`**
  in the environment, set by `main.yml` from `release-plan`'s output. Their
  command lines stay byte-identical to 1.3.0's, because a `RESUME` can target
  a commit carrying 1.3.0's tooling, whose parsers take no option. That
  tooling ignores the variable and builds its own static version, which is
  the resumed tag's version. Under `version_change` the variable is optional
  and must equal the committed version.

### The pull request title and body

- **The plan declares the title**, as one header line
  ``Pull request title: `<Conventional Commit>` ``. It is reviewed and
  approved with the plan and changed only by a plan amendment. The Controller
  reads it from `HEAD`'s committed state and `HEAD:<plan_path>`, never the
  working tree, and validates it against the committed policy.
- **Squash mode only.** Under `merge_method: "merge"` (or none), titles,
  bodies and gate texts are byte-identical to 1.3.0's, and the Controller
  never edits a pull request.
- **The title is kept in sync.** The Draft PR is created with the declared
  title (the work-item id when there is none valid), and every `PR_OPEN` step
  before acceptance edits a differing title back to the declared one. A
  `READY` pull request is never edited.
- **Readiness syncs before it samples the checks.** After readiness
  conditions 4-6 and before the checks gate: the declared title wins;
  without one, a valid current title is kept; otherwise the gate is the new
  `pr_title_invalid`. The body gains its "Accepted at" line. Any edit ends the
  step at `checks_pending`, so `READY` is never written on a `PR title` check
  that was not sampled after the edit.
- **The body becomes the squash commit's body** and has no line Git would
  parse as a trailer, so no `Workflow-*` trailer of a completed work item
  reaches `main`. The merge gate texts say "Squash and merge".
- **The forge gains one write**, `edit_pr` (`gh pr edit`), which re-reads the
  pull request and refuses unless it shows the new values. There is still no
  merge operation: a human merges.

### Squash close-out and the explicit base

- **`MERGED_SQUASHED`**, a new non-terminal state leading to `CLOSED`. It is
  written only under the binding's own `merge_method: "squash"`, and only when
  the merge is verified to be a squash of the reviewed head `h`: the merge
  commit is on the fetched trunk and has one parent `p`; its subject is the
  pull request's title followed by ` (#<number>)`; it does not carry `h`'s
  author, author date and full message (a rebase does); and its tree is `h`'s
  tree when `p` is an ancestor of `h`, or else the tree
  `git merge-tree --write-tree p h` produces.
- **Anything it cannot verify is `MERGED_REWRITTEN`**, 1.3.0's terminal state
  and gate: a conflict, a different tree, an edited squash subject, a rebase,
  or a configured merge driver (the Controller never runs one). An
  undecidable read, such as Git older than 2.38 on the `merge-tree` path or a
  failing `merge-tree`, refuses and writes nothing, because writing a
  terminal state there would make a Git upgrade useless.
- **Merge mode is unchanged.** Under `merge`, any head off the trunk is
  `MERGED_REWRITTEN` exactly as in 1.3.0, whatever the merge was.
- **Close-out** mirrors `MERGED`, with the squash commit standing in for the
  merged head wherever trunk membership is checked. The local branch is left
  in place.
- **The explicit base.** With milestone branches enabled and a passed trunk
  start, the bootstrap command is `/milestone-plan <trunk tip>` (Workflow's
  one-argument form), for both merge methods. After a squash the previous
  milestone's completion commit is not on `main`, so the no-argument form's
  base would be refused at the next bind. Every milestone then has
  `base_commit == branch_point`. Without a policy the command stays bare.

### The cutover

- **This milestone never changes `.workflow-controller/policy.json` or
  `pyproject.toml`'s version line**, so the driving 1.3.0 Controller can run
  every step of it, readiness and close-out included. Its own pull request is
  merged with a merge commit, the last one, and classifies `NO_CHANGE` at
  1.3.0.
- **Then, in order**: the repository settings become squash-only (squash
  message "Pull request title and description"), and the "Main Protection"
  ruleset allows squash only, requires the `PR title` check and requires
  linear history; then one cutover pull request changes exactly two files,
  `pyproject.toml` (`dynamic = ["version"]`) and the policy
  (`conventional_commit`, `merge_method: "squash"`), titled
  `feat: squash merges with release versions derived from pull request titles`,
  and is squash-merged. Both steps need the user.
- **The first tag-derived release continues from `v1.3.0`.** Its range holds
  legacy commits and the cutover's `feat`, so `main.yml` classifies
  `RELEASE_DUE` 1.4.0.
- **1.4.0 must be installed before the next lifecycle step.** 1.3.0 refuses
  the post-cutover policy at every step. No work item is in flight at the
  cutover, so nothing is lost.

## Alternatives rejected

- **A registry field for the change type.** Only Workflow's generator writes
  the registry; a Controller-only change cannot add a field, and a hand edit
  breaks plan-approval coverage. The plan is already the reviewed artifact the
  Controller reads.
- **A `.dev` suffix for unreleased builds** (SignalHub's `0.0.0.dev0`). It
  would break the strict `MAJOR.MINOR.PATCH` contract that `SEMVER_RE`, the
  tag rule, the wheel filename rule and `validate_build_info` rely on.
  `BUILD_INFO.json` already tells a local build from a release.
- **Lenient patch counting** (SignalHub counts every type, and a
  non-conforming subject on `main`, as a patch, with a warning). The user
  decided that `docs`, `chore` and `ci` release nothing, and this repository
  fails closed everywhere else: an unclassifiable subject is
  `INVALID_SUBJECT`, settled only by a committed `bump_overrides` entry.
- **Flipping the policy inside the milestone.** 1.3.0 would refuse every later
  step of the milestone that makes the switch. Merging that milestone by
  squash under 1.3.0 would record `MERGED_REWRITTEN`, and bumping to 1.4.0 by
  hand inside it would be the last hand edit the decision rules out. The
  cutover pull request's `feat` title produces 1.4.0 from the tag instead.
- **Reading a `BREAKING CHANGE:` footer.** A squash body varies with settings
  and pull-request prose; only the title's `!` marks a major release, as in
  SignalHub.
- **A `--version` argument for `build` and `verify`.** It would fail
  argparse on a `RESUME` target carrying 1.3.0's tooling, so an interrupted
  pre-upgrade publication could never complete.

## Consequences

- No file holds this repository's version after the cutover, and no milestone
  or person edits one. A milestone cannot know its version before its merge,
  so its release notes are its pull request body, which becomes the squash
  commit body.
- A `docs`, `chore`, `ci` or `test` title publishes nothing. A title the
  release cannot classify fails the `PR title` check before the merge, and
  `main.yml` with `INVALID_SUBJECT` after it.
- A repository on `version_change`, or without `merge_method: "squash"`,
  keeps exactly 1.3.0's behaviour, including merge-commit gate texts and
  close-out. Only the explicit base of the next `/milestone-plan` changes for
  every repository with milestone branches enabled.
- The squash commit's subject is what the release reads. A human who edits it
  in GitHub's merge dialog gets `MERGED_REWRITTEN` at close-out.
- Rolling back to 1.3.0 after the cutover is refused on this repository;
  rolling back means reverting the cutover pull request (a `revert:` squash,
  a patch release) or running 1.4.0 or later.
- Generated release notes, auto-merge and waiting for the release remain
  future work (`docs/ROADMAP.md`).
