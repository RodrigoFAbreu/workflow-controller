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
