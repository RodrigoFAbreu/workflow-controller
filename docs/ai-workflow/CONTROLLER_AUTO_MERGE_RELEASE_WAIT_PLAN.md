# Controller auto-merge after acceptance, and the wait for the release (Revision 7)

Work item: `workflow-controller-auto-merge-release-wait`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `854d25cf4c53ec63a7272a34be31c618f04c8993` ("docs: 1.5.0 release notes (#17)"), the
tip of `main` when this plan was written, passed explicitly as `/milestone-plan 854d25c…` (after a
squash merge the next item is planned from `main`'s head with the base passed explicitly,
`docs/guide/milestone-branches.md`). The previous milestone
(`workflow-controller-settings-and-telemetry`) was accepted at `9f4859b`, squash-merged by PR #16
(`f92f31b`) and released as 1.5.0. PR #17 added the 1.5.0 notes. Neither is this milestone's work.
Lifecycle authority: this repository's installed Workflow **2.6.0**.
Driving Controller: the installed **1.5.0** package (shared pipx install, installed 2026-10-01 at the
Workflow Manager lane's W0-to-W1 gap).
Roadmap slot: `docs/ROADMAP.md` step **C4** of "At a glance", section **11.3 Auto-merge and release
wait**. Its dependencies C1, C1b and C2 are complete; C3 (the settings file this milestone extends)
is complete too.
Released baseline preserved: `workflow-controller 1.5.0` (`v1.5.0`). This milestone ships as the
minor release **1.6.0**, derived by `main.yml` from the `feat:` pull request title below.
Pull request title: `feat: auto-merge an accepted milestone and wait for its release`

## Goal

Today a milestone's last three steps need a person at the keyboard even though nothing is left to
decide: after `/accept-milestone` the Controller marks the pull request ready and stops at
`merge_pull_request`; someone presses "Squash and merge"; someone watches the `Main` run publish
the release; someone runs the Controller again to close out. Section 11.3 removes those hands while
keeping `/accept-milestone` as the last human gate (until C10).

This milestone:

1. **Merges the accepted pull request itself, at the acceptance commit only.** When a repository
   opts in through its policy (`milestone_branches.pull_request.auto_merge`), and the operator's
   settings file has not turned it off (`merge.auto`), the Controller squash-merges the ready pull
   request once GitHub reports it mergeable with green checks, through one head-bound
   `gh pr merge --squash --match-head-commit <A>` call per attempt (GitHub's `mergePullRequest` with
   `expectedHeadOid`), with the squash subject and body the close-out already verifies. It never
   enables GitHub's own auto-merge request, which a later push could redirect (revision 5,
   Decision 9).
2. **Stops and reports when something goes wrong after acceptance**, instead of guessing: a red
   check, a pull request a person turned back into a draft, a merge GitHub cannot perform (a
   conflict), or a branch head that moved past the acceptance commit. The fix loop is C10, not this
   milestone.
3. **Waits for the release.** After the squash merge is verified (`MERGED_SQUASHED`), the
   Controller classifies the squash commit with the same release transaction `main.yml` runs, waits
   while the `Main` run for that commit is still working, records the published release (tag,
   version, URL) in the binding, or stops at `release_failed` naming the failed run.
4. **Closes out and stops.** Once the release is settled, close-out runs as today (switch to the
   trunk, fast-forward, `CLOSED`), and the run ends there instead of launching the next
   `/milestone-plan` in the same run (Decision 2).
5. **Waits inside `run`, boundedly.** `run` keeps polling the pending states (checks pending, merge
   pending, release pending) for up to `merge.wait_seconds` without spending a model token, so one
   unattended `run` goes from the ready pull request to the closed-out, released milestone. `step`
   never waits.

Everything is **opt-in per repository**: a policy without the new key behaves exactly as 1.5.0,
byte for byte (Invariant I1), so the Workflow Manager lane, which runs the same shared install, is
untouched until its own policy opts in.

## Non-goals

- **The fix loop** for a red pull request after acceptance, and a changes-requested review
  reopening the work item: C10 (sections 1.8, 1.9). This milestone stops and reports.
- **Automatic acceptance or approvals**: C10. `/accept-milestone` stays the last human gate.
- **Integrating `main` into a milestone branch.** `integration_required` stays the manual procedure
  (Workflow 2.5.1 and 2.6.0 have no base-moving transition, ADR 0006 E1-E5). Readiness still gates
  it, so an unattended run stops there; the Controller merges only from the `READY` state.
- **Merge mode (`"merge"`).** Auto-merge requires `merge_method: "squash"` (the policy refuses the
  combination otherwise). The merge-commit flow is unchanged.
- **Publishing or retrying a release.** The Controller never runs `tools/release.py publish`, never
  re-runs a workflow and never pushes a tag; it reads.
- **Notifications** (C5). The new binding events are written so C5 can push them; nothing is sent.
- **Turning auto-merge on in this repository.** The policy is byte-unchanged here (I9). The opt-in
  is a later `chore:` pull request after 1.6.0 is installed (see "Migration").
- **Merge queues.** A repository that requires a merge queue is unsupported: gh then adds the pull
  request to the queue (`merge.go`, `shouldAddToMergeQueue`), and the queue writes its own commit
  message, so C.3's `--subject`/`--body` do not hold. It fails closed (revision 3): the squash
  subject fails `verified_squash` condition 3 and the record ends at the terminal
  `MERGED_REWRITTEN` gate, which never closes out. The guide states the limitation (CP6).
- **The deferred C3 items** (`status` printing `None` for `pinned identity:`, the `telemetry` label
  wording, LIR1-O1) and the Manager-reported `UnclassifiedPathError` `explain` follow-up. They are
  roadmap items of their own.

## Investigation: what happens today (measured at `854d25c`)

### Readiness ends at a human merge

`controller/milestone_branch.py` `_readiness` (a `PR_OPEN` record whose item is
`MILESTONE_COMPLETE` in the branch's committed state) checks, in order: a clean tracked tree; the
tip is exactly the acceptance commit `A`; the remote branch and the pull request head are `A`;
`<remote>/<trunk>` is an ancestor of `A` (else `integration_required`); in squash mode the title
and body sync (an edit ends the step at `checks_pending`); green checks when
`ready_requires_green_checks`. Then `gh pr ready`, a re-read, `READY` with `accepted_head`, and
`_merge_gate` returns the `merge_pull_request` gate. A `READY` record's later steps
(`_branch_cells`) re-read the pull request: still open, `merge_pull_request` again; merged,
`_pr_left_open` → `_merged_handling` → `MERGED_SQUASHED` (after `verified_squash`'s five
conditions) → `_close_out_on_branch` (switch, fast-forward, `CLOSED`) → `_after_close` → the trunk
start → `Proceed(action="closed_out", base=<main tip>)`, after which `job.execute_step` decides
`/milestone-plan <base>` and launches it in the same step. From the trunk side the same records
close out through `_close_out_from_trunk`/`_close_trunk_step3`.

### "The Controller never merges" is a tested invariant

- `docs/guide/automation.md` ("Never merges, never rewrites"): "No Controller code path merges a
  pull request or pushes to the trunk ref, and the GitHub boundary (`controller/forge.py`) has no
  merge operation."
- `tests/test_no_rewrite_invariants.py` (I3 of the trunk plan) scans every argv literal in
  `controller/*.py` and fails on the element pair `"pr", "merge"`.
- `docs/ROADMAP.md` 7.6: "The adapter may observe merge state but must not auto-merge unless a
  future repository policy explicitly changes that invariant." This milestone is that explicit
  policy change, and narrows the invariant rather than deleting it (Design B.3).
- `docs/adr/0007-tag-derived-versions-and-squash-merges.md` lists "auto-merge and waiting for the
  release" as future work.

### What `gh` and GitHub offer (gh 2.101.0, measured 2026-10-01)

- `gh pr merge <n> --squash --match-head-commit <sha> --subject <s> --body <b>` merges the pull
  request now; `--auto` instead asks GitHub to merge later, when the requirements are met.
- `gh pr view --json` has `autoMergeRequest` (an object, or `null`) and `mergeStateStatus`
  (`CLEAN`, `BLOCKED`, `BEHIND`, `DIRTY`, `UNSTABLE`, `HAS_HOOKS`, `DRAFT`, `UNKNOWN`). Measured on
  merged PR #17: `{"autoMergeRequest":null,"mergeStateStatus":"UNKNOWN","state":"MERGED"}`.
- `gh run list --commit <sha> --branch <trunk> --event push --workflow <file> --json
  databaseId,workflowName,status,conclusion,attempt,url` lists the trunk runs of one workflow for
  one commit. Measured for `854d25c` without `--workflow`: two runs, `Main` and `Workflow
  conformance`, both `completed`/`success`, attempt 1.
- **gh's two mutations** (gh 2.101.0's source, `pkg/cmd/pr/merge/merge.go` and `http.go` at tag
  `v2.101.0`, read in revision 2): `NewMergeContext` sets
  `autoMerge: opts.AutoMergeEnable && !isImmediatelyMergeable(pr.MergeStateStatus)`, and
  `isImmediatelyMergeable` is true for `CLEAN`, `HAS_HOOKS` and `UNSTABLE`. With `autoMerge` false
  gh sends the plain `mergePullRequest` mutation, with `expectedHeadOid` (`--match-head-commit`),
  `commitHeadline` (`--subject`) and `commitBody` (`--body`); with it true, it sends
  `enablePullRequestAutoMerge`. **Without `--auto`, `autoMerge` is false whatever the state, so gh
  never enables an auto-merge request** (revision 5). `mergePullRequest` merges at once or is
  refused; GitHub refuses it when the head is not `expectedHeadOid` at the moment of the merge, so
  a merge it performs is a merge of exactly that commit.
- **An auto-merge request is not bound to the head it was enabled at** (revision 5, external
  review). `expectedHeadOid` on `enablePullRequestAutoMerge` is checked when the request is made.
  GitHub's documentation ("Automatically merging a pull request") states that a push by someone
  without write permission disables auto-merge, so a push by someone with write permission leaves
  it enabled, and GitHub then merges the new head once its checks pass. A Controller that polls
  can only notice that afterwards. This is why the design never enables a request (C, Decision 9).
- **`UNKNOWN`**: GitHub computes mergeability asynchronously and commonly reports `UNKNOWN` for a
  short while after a state change -- such as the `gh pr ready` readiness performs just before C.2.
  The Controller sends nothing while the state is not computed (C.2).

### The release is observable read-only

`controller/release_txn.py` `classify(ctx, commit)` -- the function `tools/release.py classify`
runs in `main.yml`'s `release-plan` job -- classifies a trunk commit: `ALREADY_RELEASED` (its tag
and published release exist and verify), `NO_CHANGE` (`docs:`/`chore:`/`ci:`/`test:`),
`ABANDONED_VERSION`, `RELEASE_DUE`/`RESUME` (publication still to happen), or a failure state
(`INVALID_SUBJECT`, `RELEASE_MISMATCH`, the collisions, `BASELINE_UNRELEASED`,
`INVALID_TRANSITION`, `ABANDONED_TAG_INCONSISTENT`). It fetches the trunk and the tags, reads
`ls-remote` and the forge's releases, and writes no ref other than fetched remote-tracking refs and
tags. It is **not** entirely read-only, though: for a tag at the commit with a published release it
calls `release_problems`, which downloads the release's assets into a scratch directory and runs
the policy's `verify` command (`python3 tools/release.py verify-wheel ...` here) on each artifact,
in whatever worktree the caller passes -- repository code, run on the operator's machine, from a
checkout that need not be the classified commit. CI runs it at the checked-out target commit; the
Controller must not (Design D.2). `main.yml` runs on every push to `main` and publishes from the
`publish` job only for `RELEASE_DUE`/`RESUME`, after its own `verify` job. Its
`concurrency: {group: main-release, cancel-in-progress: false}` (`.github/workflows/main.yml:28-30`)
never cancels the **running** run, but GitHub keeps at most one **pending** run per group and
cancels it when a newer run queues (corrected in revision 6; the file's own header comment, "Runs
never cancel one another", says less than that). So a push to `main` that lands while an earlier
run is in progress can have its own run cancelled by a later push, whose run then classifies the
later commit and publishes the release that covers both (D.3's superseded rows). More generally,
any later run classifies its own commit, so it can tag a version an earlier failed run left untagged
or resume a tag an earlier run left unpublished (D.3's recovery row, revision 7).

### The settings file has integers only

`controller/settings.py` `TABLE` holds ten keys, all `int`/`optional-int` apart from `routing`;
`TABLE_GENERATION` is 1. A boolean setting needs a new type, and adding keys raises the table
generation (A.3's compatibility rule).

### `docs/TECHNICAL_DECISIONS.md`

This repository has no `docs/TECHNICAL_DECISIONS.md`, so there is no "Open decision" row this plan
could silently finalize. The decisions it makes are listed under "Decisions for the reviewer and
the user".

## Invariants

- **I1 -- opt-in, otherwise byte-identical.** A binding whose policy snapshot has no
  `auto_merge: true` behaves exactly as 1.5.0: the same gates, messages, events, record fields and
  close-out, and `/milestone-plan` is launched after a close-out exactly as before. Every existing
  milestone-branch test passes unchanged. The one recorded difference is the settings echo
  (revision 6): every job record's `controller_settings` block lists one `sources` and one
  `values` entry per `settings.TABLE` key (`cli._controller_settings_block`, `controller/cli.py:1078`,
  written at `controller/job.py:4812-4815`), so the no-policy golden
  (`tests/golden/no_policy_lifecycle.json`) gains the three `merge.*` keys in each job record's
  `sources` and `values`, and nothing else. CP1, which adds the rows, regenerates it and pins that
  the diff is exactly those additions.
- **I2 -- only the accepted head merges automatically.** The Controller merges only a `READY`
  record's pull request, only through a `mergePullRequest` call bound to `accepted_head`
  (`--match-head-commit`), which GitHub performs atomically or refuses (revision 5). It never
  enables GitHub's auto-merge request, so nothing it leaves behind can merge a later head, and it
  never acts against a person's hold: a pull request turned back into a draft is not merged (C.2).
- **I3' -- the Controller merges only the accepted head, only through GitHub, and never writes the
  trunk** (narrowed from I3; reworded in revisions 2 and 5). The only `gh pr merge` argv in
  `controller/` is in `controller/forge.py`, and it carries `--squash` and `--match-head-commit`
  and none of `--auto`, `--disable-auto`, `--admin` or `--delete-branch`. The merge is GitHub's
  squash merge, under the repository's branch protection and required checks, of exactly the
  acceptance commit `A`, sent only for a `READY` record, after readiness saw `A` everywhere and
  green checks, and only on a step's own re-read showing `A`, green checks and a `CLEAN` or
  `HAS_HOOKS` merge state. What is guaranteed is therefore: a merge only at `A`, only after
  `/accept-milestone` and only after the Controller has seen green checks; never "the Controller
  never merges". No code path pushes to the trunk ref, closes a pull request, or deletes a branch.
- **I4 -- the release is read, never made.** The release wait calls only read operations
  (`release_txn.classify` with asset verification off, `gh release view`, `gh run list`); it never
  builds, tags, publishes or re-runs anything, never downloads a release asset, and never runs a
  policy command (`build`, `verify`). Its only local writes are the tag and remote-tracking fetches
  `classify` already makes.
- **I5 -- every refusal and stop names its exit.** Each new gate states what happened, what the
  Controller did not do, and the action that clears it, like every existing branch gate.
- **I6 -- waiting is bounded, interruptible and model-free.** `run` waits at most
  `merge.wait_seconds` per step, polls at `merge.poll_seconds`, launches no worker while waiting,
  and an interrupt leaves the binding at its last atomically written state. `step`, `explain`,
  `status` and `inspect` never wait.
- **I7 -- unchanged:** a readied pull request stays at the accepted head; the Controller pushes
  nothing past it while the record is `READY`.
- **I8 -- the binding keeps the policy it was bound with.** `auto_merge` is read from the binding's
  policy snapshot (`binding_policy(record)`), like the merge method and the release-notes location,
  so a policy edit mid-milestone never changes an in-flight milestone. The settings switch is read
  at each step (Decision 1).
- **I9 -- no repository-configuration change.** `.workflow-controller/policy.json`,
  `pyproject.toml`, `setup.py` and `.github/workflows/` stay byte-unchanged.

## Design

### A. The two switches (CP1)

**A.1 The repository opts in.** `milestone_branches.pull_request` gains two optional keys:

```json
"pull_request": {"draft": true, "ready_requires_green_checks": true, "merge_method": "squash",
                 "auto_merge": true, "release_workflow": "main.yml"}
```

- `auto_merge` is a boolean, default `false`. `repo_policy.MilestoneBranches` gains
  `auto_merge: bool = False`.
- `true` requires `merge_method: "squash"` and `ready_requires_green_checks: true`; otherwise the
  policy is inadmissible (`InvalidRepositoryPolicyError`, naming the field and the reason: the
  squash verification and the release wait are squash-mode only, and the Controller must have seen
  green checks before it merges).
- `release_workflow` (revision 5) names the workflow file under `.github/workflows/` whose trunk
  push run publishes the release; default `"main.yml"`, so this repository needs no policy change
  (I9). A non-empty file name without a `/`; anything else is inadmissible.
  `repo_policy.MilestoneBranches` gains `release_workflow: str = "main.yml"`. Only the release wait
  reads it (D.3).
- `true` with `release.enabled: false` is admitted: auto-merge then closes out without a release
  wait (A.3's "no release configured" row).
- Controller 1.5.x refuses a policy that has either key (an unknown key), which is the intended
  fail-closed behaviour for an older Controller; the migration section states the order.

**A.2 The operator can turn it off.** The settings file gains three rows (`TABLE_GENERATION` 1 →
2):

| Key | Type | Default | Bounds | Command-line override |
|---|---|---|---|---|
| `merge.auto` | boolean | `true` | -- | none |
| `merge.wait_seconds` | integer | `3600` | 0 to 86400 | none |
| `merge.poll_seconds` | integer | `30` | 10 to 600 | none |

- A new type `bool`: only JSON `true`/`false`; an integer is refused where a boolean is expected,
  as a boolean already is where an integer is (A.2 of the settings plan). `settings show`, the
  fill, `_defaults_written`, `settings clean` and the unknown-key rule treat the rows like any
  other.
- `merge.auto: false` means: the Controller does not merge; readiness ends at `merge_pull_request`
  as in 1.5.0, and a person merges. The release wait and the stop after close-out still follow the
  binding's policy (Decision 1), because they do not act on GitHub.
- `merge.wait_seconds: 0` means `run` does not wait: a pending state ends the step at its gate,
  as `step` always does.
- The values reach `milestone_branch` through `Context` (new fields `auto_merge: bool`,
  `wait_seconds: int`, `poll_seconds: int`, defaults `True`/`0`/`30`), set by `job.execute_step`
  from `cli._effective(args)`. `explain`'s `predict` reads `merge.auto` the same way.

**A.3 When auto-merge applies.** `auto_merge_applies(record, ctx)` is true when the binding's policy
snapshot has `auto_merge: true` and `ctx.auto_merge` is true. `release_wait_applies(record)` is
true when the snapshot has `auto_merge: true` (the setting does not matter). Both are false for
every record bound before the repository opted in.

### B. The forge surface (CP2)

**B.1 Reads.** `forge.PullRequest` gains `merge_state: str` (`mergeStateStatus`, any string;
unknown values are passed through and treated as "not mergeable yet"). `PR_FIELDS` gains it.
`autoMergeRequest` is not read: the Controller never makes such a request, and one a person makes
does not change what the Controller does (C.2). A new read
`commit_runs(commit, branch, workflow) -> tuple[Run, ...]` runs
`gh run list --commit <commit> --branch <branch> --event push --workflow <workflow> --json
databaseId,workflowName,status,conclusion,attempt,url --limit <forge.pr_list_limit>` and refuses
(undecidable) on a full page, like `list_prs`. `Run` is `{id, workflow, status, conclusion,
attempt, url}`.

**B.2 The one write.** `merge_squash(number, *, head, subject, body)`:
`gh pr merge <number> --squash --match-head-commit <head> --subject <subject> --body <body>`. It
does not re-read; the caller re-reads with `view_pr` (Design C). There is no `--auto` and no
`--disable-auto` call anywhere (revision 5).

**B.3 The narrowed static guard.** `tests/test_no_rewrite_invariants.py` keeps failing on
`"pr", "merge"` everywhere except `controller/forge.py`, and there only for an argv **list literal**
that also holds `"--squash"` and `"--match-head-commit"` and none of `"--auto"`, `"--disable-auto"`,
`"--admin"` or `"--delete-branch"`. In `controller/forge.py` it also fails on a `"merge"` string
element that is not a plain literal element of such a list (built by concatenation, formatting or
a variable). That detection is new behaviour CP2 writes (revision 3): today's `argv_literals`
(`tests/test_no_rewrite_invariants.py:45`) maps a non-constant element to `None`, so the existing
scan does not catch `"mer" + "ge"` anywhere. A synthetic-source test pins both directions (a bare
`["gh", "pr", "merge", "1"]` still fails, as do the allowed shape plus `"--auto"` and a
concatenated `"mer" + "ge"` in forge; the allowed shape passes). `"pr", "close"`,
`"release", "delete"`, `--clobber` and `gh api` stay forbidden.

**B.4 The fake.** `tests/fake_gh.py` models `pr merge --squash --match-head-commit` (merges, writing
the merged state and the squash subject and body, when the head matches and the fake's merge state
is `CLEAN`/`HAS_HOOKS`; otherwise refuses the way GitHub does), `mergeStateStatus` in
`pr view`/`pr list`, and `run list --commit --workflow`. A test can make the merge succeed while
the next `pr view` still reads `OPEN` (read lag), make it fail, or merge a pull request itself, as a
human merge is simulated today. The fake refuses `--auto` and `--disable-auto` outright, so a test
fails if either is ever sent.

### C. The merge at readiness (CP3; rewritten in revision 5)

Revisions 1-4 enabled GitHub's auto-merge request and then polled it. The external review showed
that a request outlives its `--match-head-commit` check (investigation, "What `gh` and GitHub
offer"): a push by someone with write access leaves it enabled and GitHub merges the new head
before the next poll. Revision 5 therefore never enables a request. The Controller itself sends
one head-bound merge whenever it sees the pull request mergeable, and nothing is left standing
between its steps.

**C.1 Where.** The two places that today return `_merge_gate(record, tip)` -- the end of
`_readiness` and the `READY` cell of `_branch_cells` -- call `_merge_step(ctx, key, record, head)`
instead. When `auto_merge_applies` is false it returns `_merge_gate` unchanged (I1). Otherwise it
runs C.2-C.3 and returns a gate, or signals "merged" so the cell loop re-reads the pull request and
continues into the existing merged-PR handling.

The merge is branch-side only (revision 6). With `HEAD` on the trunk, a `READY` record goes through
`_close_out_from_trunk`, which refuses on an open pull request with the exit "switch to it"
(`controller/milestone_branch.py:1736-1744`), unchanged. An unattended `run` started from the
trunk therefore stops there and merges nothing; the guide says so (CP6).

**C.2 The decision.** For a `READY` record (`accepted_head` `A`), each step re-reads the pull
request and its checks. The order follows `_branch_cells`' existing `READY` cell
(`controller/milestone_branch.py:673-683`, corrected in revision 6): the cell reads the pull
request, sends a pull request that is no longer open to `_pr_left_open`, and runs
`_observe_branch(..., sync=False)` once, before `_merge_step` is reached. The table is evaluated top
to bottom and the **first matching row** decides; the first three rows are the cell's existing
code, unchanged:

| Observation | Outcome |
|---|---|
| `MERGED` | continue into the merged-PR handling (and D) |
| `CLOSED` | the existing `_pr_left_open` path (`PR_CLOSED_UNMERGED`) |
| `<remote>/<branch>` is not an ancestor of the local tip (C.4 case a) | `_observe_branch`'s existing "Update branch" refusal (`milestone_branch.py:733-739`); not waitable; nothing is sent |
| the local tip is not `A` (C.4 cases b and c) | gate `post_acceptance_commits` with its `READY` text (C.4, F); not waitable; nothing is sent |
| the pull request's head is not `A` (the tip and `<remote>/<branch>` are `A`, but GitHub's read lags, or the remote branch is gone) | gate `pr_head_not_accepted` (waitable, E); nothing is sent |
| the pull request is a draft | gate `merge_held` (C.5); nothing is sent |
| `_checks_gate` returns a gate (readiness condition 7, the same function, `milestone_branch.py:1504-1526`) | that gate unchanged, nothing sent: `checks_failing` (not waitable, Decision 5), then `checks_pending` for a pending check or none reported (waitable, E), then `checks_cancelled` (not waitable: a person re-runs the cancelled checks) |
| `merge_state == "DIRTY"` | gate `merge_pending` at once (not waitable), naming the conflict |
| `merge_state == "BEHIND"` | gate `integration_required` at once (not waitable) |
| a `merge` field in state `accepted` | gate `merge_pending` (waitable): "GitHub accepted the merge; it is not visible yet", with the exits for one that never appears: merge the pull request by hand with "Squash and merge", or recover the binding with `milestone-binding`; nothing is sent |
| `merge_state` `CLEAN` or `HAS_HOOKS`, `attempts` below three | send the merge (C.3) |
| `merge_state` `CLEAN` or `HAS_HOOKS`, `attempts` at three | C.3 step 4's refusal; nothing is sent |
| otherwise (`BLOCKED`, `UNSTABLE`, `UNKNOWN`, a stale `DRAFT`, an unknown value) | gate `merge_pending` (waitable), naming `merge_state`; nothing is sent |

`UNKNOWN` and a stale `DRAFT` (the pull request is no longer a draft, but GitHub has not recomputed
since `gh pr ready`) are "GitHub has not computed mergeability yet". `BLOCKED` with every check
green (revision 4) means a branch-protection requirement other than the checks -- a required
approving review, conversation resolution, signed commits or a deployment (`reviewDecision` is not
read, `forge.PR_FIELDS`); it stays waitable, because a person can supply the review while the run
waits, and its text says so. `UNSTABLE` (a non-required status that is not green, which
`pr checks` did not report as failing) is not merged: the Controller merges only on a state with
nothing outstanding, and a person can merge it by hand.

`BEHIND` is GitHub's report that branch protection requires the branch to be up to date with the
trunk and it is not, so no merge can happen until someone integrates the trunk -- the integration
this milestone excludes (Non-goals). The gate names the existing `integration_required` procedure.
A trunk that moved without `BEHIND` (a repository that does not require up-to-date branches) is
**not** gated: GitHub reports the pull request mergeable, the Controller merges, and
`verified_squash` verifies such a merge through `git merge-tree` when the squash parent is not an
ancestor of `A` (`controller/milestone_branch.py:999-1011`).

**C.3 Sending the merge.**

1. write the intent: `merge = {"state": "sending", "head": A, "attempts": k + 1,
   "last_attempt_at": now}` (`k` is the previous `attempts`, `0` without a `merge` field) and, for
   the first attempt, the event `merge_sent`;
2. `merge_squash(n, head=A, subject=f"{pr.title} (#{n})", body=pr.body)` -- the subject and body are
   exactly what `verified_squash` condition 3 and the release-notes reader expect, so the merge does
   not depend on the repository's default squash-message setting (Decision 4). gh sends the plain
   `mergePullRequest` with `expectedHeadOid` = `A` (no `--auto`, so never a request), and GitHub
   merges `A` or refuses;
3. on success (gh exits 0, GitHub has merged): write `merge.state = "accepted"` and the event
   `merge_accepted`, then re-read: `MERGED` → continue (merged-PR handling, then D); still `OPEN`
   (GitHub's reads can lag its writes) → the waitable `merge_pending` of C.2's `accepted` row. An
   `accepted` record is never sent again;
4. on a `ForgeError`: re-read. `MERGED` → continue as in 3 (a race the merge won). Otherwise the
   record keeps `state: "sending"` and its incremented `attempts`, the event `merge_refused` is
   written once per distinct forge message, and: below three attempts, the waitable `merge_pending`
   naming the forge's message (C.2 decides again at the next poll, and sends again only on a
   re-read showing `A`, green checks and `CLEAN`/`HAS_HOOKS`); at three attempts, a refusal
   (`BranchBindingError`) with the forge's own message and the exits: **merge the pull request on
   GitHub with "Squash and merge"** (the next step closes out as for any merge). `attempts` is
   never reset, so the Controller sends nothing more for this record; the message names the
   forge's cause (for example squash merging disabled in the repository settings) for the person
   doing the merge.

A record found at `state: "sending"` (a later poll, or a crash anywhere after step 1) starts at
C.2's re-read, and the table decides as for any step: a re-send happens only through the
`CLEAN`/`HAS_HOOKS` row, so a crash between step 1 and step 2, or between step 2 and step 3, is safe.
Every send is a one-shot merge bound to `A`: a duplicate of a merge that already happened is refused
by GitHub ("already merged") and the step-4 re-read adopts `MERGED` once it is visible; a duplicate
of a merge that did not happen is simply the merge. Nothing a send leaves behind can merge later, so
no interval between attempts is needed (the revision 2-4 `REQUEST_VISIBILITY_SECONDS`, the
stale-state rejection rows and the requested/enabled/withdrawn states are gone). One send per step
at most; at the default `merge.poll_seconds` the three-attempt budget spans at least a minute.

Functional review's live probe records gh's and GitHub's exact refusal texts (a stale merge state,
an already merged pull request) for the guide; no code keys on them.

**C.4 The head moved** (rewritten in revision 6). 1.5.0 has no head check for a `READY` record:
`post_acceptance_commits` is a readiness gate (`_readiness`, a `PR_OPEN` record whose local tip is
past `A`), and the `READY` cell returns `_merge_gate(record, tip=head.commit)`, whose suffix
mentions local commits that are not pushed. In an auto-merge binding each way the head can leave
`A` after `READY` has one outcome, and none sends anything:

- **(a) a push to the branch on GitHub only** (a person pushes, or presses "Update branch"): the
  remote branch is no longer an ancestor of the local tip `A`, so the cell's `_observe_branch`
  refuses with its existing "Update branch" refusal before `_merge_step` runs. It is a refusal,
  not a gate, so it is not waitable; its exit is the existing one (bring the branch and the remote
  branch back into a fast-forward relation), or merge by hand.
- **(b) a local commit that is also pushed** (the local tip and the remote branch both past `A`):
  `_observe_branch` passes, and C.2's local-tip row gates `post_acceptance_commits` with a new
  `READY` text: "`<branch>` has commits after the acceptance commit `A`: <list>. The pull request
  was marked ready at `A`, and the Controller merges only `A`, so it sends no merge while the
  branch carries later commits. A human decides: merge anyway on GitHub with "Squash and merge",
  after which the merged-PR handling converges; otherwise this gate persists". Not waitable.
- **(c) a local commit that is not pushed** (the pull request still shows `A`): the same gate and
  text. Merging `A` would be a merge of the accepted head, but the Controller does not merge while
  the operator's branch has moved on, and 1.5.0's suffix about unpushed commits only applies when
  a person merges.

The code stays `post_acceptance_commits` (no new `GATE_CODES` entry); only its message differs at
`READY`, because readiness's text ("a pull request is marked ready only at the accepted head")
describes a pull request that is not ready yet. If a person merges anyway, the merged-PR handling
decides as today: it finds the acceptance commit and verifies the squash, or records
`MERGED_REWRITTEN`. The Controller holds no request that could merge the new head, so there is
nothing to withdraw (revision 5). A request a person enabled on GitHub themselves is theirs; the
Controller neither reads nor withdraws it, and a merge it makes is handled like any hand merge.

**C.5 `merge_held`.** A `READY` record whose pull request is a draft again: a person converted it
back to hold the merge. This is the person's lever (the other is `merge.auto: false`), and the
Controller does not override it (I2). The gate names the exits: mark the pull request ready for
review on GitHub (the next step merges it), or merge it by hand with "Squash and merge". It is not
waitable, and the record is not changed.

**C.6 The setting turned off mid-milestone.** With `merge.auto: false` a `READY` record ends at
`merge_pull_request` as in 1.5.0, whatever its `merge` field says. Nothing is pending on GitHub, so
there is nothing to withdraw.

### D. The release wait, then close out and stop (CP4)

**D.1 Where.** In a binding where `release_wait_applies`, `_release_wait(ctx, key, record)` runs
for a `MERGED_SQUASHED` record before close-out step 3, on both sides: at the top of
`_close_out_on_branch` and of `_close_trunk_step3`. It returns `None` (settled: close out) or a
gate, and is read-only apart from the record write. A record with a `release` field is settled and
skips it.

A person can still merge an auto-merge binding by hand in a way that is not the verified squash.
Revision 2 decides each merged state (first column from `_merged_handling`,
`controller/milestone_branch.py:930`):

| Merged state in an auto-merge binding | Release wait | Stop after close-out |
|---|---|---|
| `MERGED_SQUASHED` | runs (D.2, D.3) | yes (D.4) |
| `MERGED` (a "Create a merge commit" by hand, if the repository allows it; or a PR-less close-out) | skipped; the record gets `release = {"state": "SKIPPED", "reason": "not a verified squash merge"}` and event `release_wait_skipped` | no: close-out continues exactly as 1.5.0, launching `/milestone-plan` |
| `MERGED_REWRITTEN` (an edited title or body in the merge dialog, or a rebase merge) | skipped: the state is terminal and shows its existing one-time gate, which never closes out (`TERMINAL_STATES`, `milestone_branch.py:89`) | not applicable (no close-out) |
| `MERGED_BEFORE_ACCEPTANCE` | unreachable from `READY` (an accepted record has `A`); unchanged | unchanged |

The release wait is skipped for `MERGED` because its premise -- the trunk commit `main.yml`
classifies is the squash commit whose subject is the pull request title -- does not hold: a merge
commit's subject is GitHub's "Merge pull request #n ...", whose classification is the repository's
CI's concern and not a release this milestone promises. A person who merged by hand that way has
taken the merge over, and the step behaves as 1.5.0 did for that merge. D.4's stop therefore keys
on "the release wait ran", not on the binding.

**D.2 Classifying the squash commit `m`.** `release_txn.classify(ReleaseContext(repo_root,
policy=<policy committed at m>, forge=ctx.forge(...), git_runner=ctx.runner), m,
verify_assets=False)` -- `git_runner` is passed so the release wait's Git calls use the
preflight's own runner and test seam (`ReleaseContext.git_runner` defaults to `None`). `classify` gains
the keyword `verify_assets: bool = True`; `False` skips `release_problems` for a tag at the commit
with a published release and returns `ALREADY_RELEASED` with the detail "published (assets not
verified by the Controller)". Every existing caller (`tools/release.py`, `main.yml`) keeps the
default, so CI's classification is unchanged; the asset verification stays CI's, which published
only after its own `verify` job (I4). The table:

| Policy at `m` / classification | Outcome |
|---|---|
| no policy, or `release.enabled: false` | settled: `release = {"state": "NONE"}` |
| `ALREADY_RELEASED` | settled: `release = {"state", "version", "tag", "url"}`, event `released` |
| `NO_CHANGE`, `ABANDONED_VERSION` | settled: `release = {"state", "version", "detail"}`, event `release_settled` |
| `RELEASE_DUE`, `RESUME` | D.3 |
| `COLLISION_TAG_ELSEWHERE` whose tag commit is a trunk descendant of `m` (revision 6) | D.3's superseded rows: a later run published, or is publishing, the release that covers `m` |
| any other state (including `COLLISION_TAG_ELSEWHERE` at a commit that is not a descendant of `m`) | gate `release_failed` with the classification's `detail` and `problems` |
| `ReleaseTransactionError` | gate `release_failed` naming the error |

`ForgeUndecidableError`/Git errors refuse as everywhere else (a network failure is not a failed
release).

**D.3 Publication pending** (rewritten in revision 5). Only the publishing workflow's runs decide:
`commit_runs(m, trunk, workflow=<binding policy's release_workflow>)` (A.1, B.1; the snapshot's
value, I8). Runs of other workflows, such as `Workflow conformance`, are never read, so neither
their failure nor their success can stop the wait:

- no publishing run reported yet, or any not `completed` → gate `release_pending` (waitable, E),
  naming the runs and their states. There is no time-based failure: the publishing run may be
  registered late, and the wait budget (E.2) is the bound; when it expires the step ends at
  `release_pending`. Its message also names the exit for a commit the workflow does not run for
  (revision 3): "if no run of `<file>` appears, publish by hand with `tools/release.py`
  (`docs/guide/ci-and-releases.md`, 'Checking a release by hand'); the next step classifies the
  commit again and settles". Every poll classifies first (D.2), so a hand publication clears the
  gate;
- every publishing run `completed` → classify `m` once more (the publish may have finished between
  the two reads); settled → as D.2. Still `RELEASE_DUE`/`RESUME`:
  - a publishing run whose conclusion is not `success` → gate `release_failed`, naming each such
    run with its URL. Exits: "Re-run failed jobs" on that run (the release transaction resumes
    safely, `RESUME`), or publish by hand with `tools/release.py`;
  - every publishing run succeeded → gate `release_failed` whose message says, instead of naming a
    failure, that the publishing workflow completed successfully for `m` and published nothing --
    which `main.yml` does only when its own classification differed (for example, the policy at `m`
    is not the one the Controller read) -- with the same exits.

**Superseded publication** (revision 6). `main.yml`'s concurrency group cancels a *pending* run when
a newer one queues (investigation), so `m`'s run can be cancelled by a later push to the trunk,
whose run classifies the later commit and publishes the release covering `m`, tagged at that
descendant. Classifying `m` then gives `COLLISION_TAG_ELSEWHERE` (the version's tag at a commit
that is not `m` or an ancestor, `controller/release_txn.py:510-512`), or `RELEASE_DUE` when the
later commit raised the version further. Neither is a failed release. The release wait therefore
computes the **covering tag**: among the remote's tags that the policy's `tag_format` matches (the
same `ls-remote` read and matching `classify` makes), the lowest-version one whose commit `t` is a
trunk descendant of `m` (`m` an ancestor of `t`, `t != m`). On a linear trunk no tag lies between
`m` and `t`, so `t`'s release range, which starts at its highest ancestor tag, includes `m`. These
rows are checked before any `release_failed` that D.2 or the bullets above would give:

- a covering tag whose release is published (`view_release`, not a draft) → settled:
  `release = {"state": "SUPERSEDED", "version", "tag", "url", "commit": t}`, event
  `release_settled`;
- a covering tag without a published release → `commit_runs(t, trunk, workflow)` decides as the
  bullets above do for `m`: not all `completed`, or none reported → `release_pending` naming `t`'s
  runs; all completed → the recovery rows below, with `t` as the commit whose runs finished.

**Recovery by a later trunk run** (revision 7). A run of `main.yml` at a later trunk commit can still
publish the release covering `m` after the runs at `m` (or at the covering tag's `t`) have finished
without publishing it: `main.yml` classifies its own commit, so a later run tags its commit when
`m`'s version still has no tag (`RELEASE_DUE`, a covering tag), or resumes the existing tag at `m`
or `t` when the version is unchanged (`RESUME`, `controller/release_txn.py:546-548`), whatever the
earlier runs' conclusions were (`failure`, `cancelled`, or a success that published nothing). So
every `release_failed` that D.3 would give -- from `m`'s runs or from a covering tag's runs, the
bullets above -- is first checked against the trunk's latest run. Let `c` be the commit whose runs
all completed (`m`, or `t`) and `d` the fetched `<remote>/<trunk>` tip:

- `d` is a strict descendant of `c` (`c` an ancestor of `d`, `d != c`) → `commit_runs(d, trunk,
  workflow)`: not all `completed`, or none reported → `release_pending`, naming `c`'s finished runs
  with their conclusions and `d`'s runs ("the run for `<c>` ended `<conclusion>` without publishing;
  the run for `<d>` on the trunk can still publish the release covering `m`"), and the same exits
  `release_failed` names, so an operator who wants to act need not wait; all completed → classify
  `m` again (the later run may have just published) and settle as D.2 or the covering-tag rows do,
  else `release_failed` naming `c`'s runs and `d`'s runs that did not succeed, or the
  published-nothing text;
- `d == c`, or `d` not a descendant of `c` → `release_failed` exactly as the bullets above say.

Only the trunk tip is consulted, never each intermediate commit: `main.yml`'s concurrency group runs
one run at a time and cancels a pending one when a newer one queues (investigation), so the newest
run is the one that can still publish, and each poll re-reads the tip, so a further push moves the
recovery to its own run. "None reported" for `d` stays pending as for `m` (no time-based failure;
the wait budget, E.2, bounds it). This subsumes revision 6's cancelled-only row. A run of `m` that
did not publish, with no later trunk commit (for example, a person cancelled it), is still
`release_failed`. Each read is the same read-only kind `classify` already makes (I4).

`release_failed` is not waitable; the next step classifies again, so a release that appears later
(a hand publication, a re-run, a superseding run) settles it.

**D.4 Close out, then stop.** When the release is settled, close-out runs unchanged (I1 for the
close-out itself). Then, for a record whose release wait ran (a `MERGED_SQUASHED` record of a
binding where `release_wait_applies`, D.1's table), the step ends instead of going
on to the trunk start: `_after_close` returns `Proceed(action="closed_out", stop=True)` (the
branch side), and `_on_trunk` returns the same when its reconciliation closed out such a binding
(the trunk side). `Proceed` gains `stop: bool = False`. `job.execute_step` returns a no-action
outcome for it (exit 0, the run's `no_action` event with reason `closed_out_released`, the release
named), and `run` stops, like any no-action outcome. Like every no-action outcome today
(`controller/job.py` `_execute_step_locked`'s docstring), it writes **no job record** (revision 5):
the binding's record and events and the run log evidence the close-out and the release. The next
`run` or `step` starts from the trunk and plans the next milestone with `/milestone-plan <main tip>`
exactly as today (Decision 2).

**D.5 Events and the record.** The binding's events file gains `merge_sent`, `merge_accepted`,
`merge_refused` (once per distinct message), `released`, `release_settled`, `release_wait_skipped`
and `release_failed` (the last once per distinct detail, not per poll). The record gains `merge` and
`release` (additions only; `status --json` and `inspect` carry them, F).

### E. The bounded wait in `run` (CP5)

**E.1 Waitable gates.** In a binding where `auto_merge_applies` (or, for `release_pending`,
`release_wait_applies`): `checks_pending` and `pr_head_not_accepted` (at readiness and in C.2),
`merge_pending` (not for `DIRTY`; including C.2's not-yet-computed state, `BLOCKED` text and
`accepted`-not-visible row, and C.3's refused-below-three-attempts outcome) and `release_pending`
(including D.3's superseded-pending and later-run recovery rows). `integration_required`, `merge_held`, `checks_failing`,
`checks_cancelled`, `post_acceptance_commits` and `_observe_branch`'s refusal are not waitable.
Every other gate ends the step at once.

**E.2 The loop.** `milestone_branch.waiting_preflight(ctx, requested_work_item_id, sleep, monotonic)`
wraps `repository_preflight`: while the outcome is a waitable gate and less than
`ctx.wait_seconds` has passed since the first waitable gate of this step, it emits the run event
`waiting` (gate code and deadline; once per gate code), sleeps `ctx.poll_seconds`, and runs the
preflight again. Otherwise it returns the outcome. The budget is one per step, across gate
changes; the step records only its final outcome, as today: one job record for a gate, none for a
no-action outcome such as D.4's stop (revision 5).

The seam (revision 6): `job.execute_step` has no parameter saying which command called it
(`controller/job.py:4548-4561`, one caller at `controller/cli.py:1067`). It gains two keywords,
`wait_seconds: int = 0` and `on_wait: Callable[[str, str], None] | None = None`. `cli` passes
`wait_seconds=<merge.wait_seconds>` for `run` and leaves `0` for `step` (I6), and passes
`on_wait=lambda code, deadline: run.event("waiting", gate=code, deadline=deadline)` when a run object
is open. `execute_step` puts `wait_seconds` on the `Context` it builds (A.2's `wait_seconds` field,
so the setting alone never makes `step` wait) and passes `on_wait` to `waiting_preflight`, which
calls it once per gate code; `milestone_branch` itself never holds the run handle.

Each poll re-runs the full preflight (the fetches, `gh pr view`, `gh pr checks`, and in the
release wait `gh run list` and the release reads). At the 10 s lower bound of `merge.poll_seconds`
that is at most 360 preflights an hour, a few `gh` calls each -- well inside GitHub's
authenticated 5000-requests-an-hour REST and GraphQL budgets; at the default 30 s it is 120. The
bound is what keeps it there, so it is not lowered without re-doing this arithmetic.

**E.3 Interrupts.** There is no existing interruptible wait in the step path to reuse (corrected
in revision 2): the only sleeps today are the test-hook pause (`cli._await_pause_file`,
`controller/cli.py:1175`) and the lock poll (`controller/job.py:4916`). The wait is a plain
`time.sleep(ctx.poll_seconds)` (injected as `sleep` for tests), and the mechanism is the one every
Ctrl-C in a step already uses: `KeyboardInterrupt` propagates out of `execute_step` through the
lifecycle lock's context manager, which releases the lock, to `cli.main`, whose `finally` calls
`_close_open_run(..., interrupted=True)` → `run.interrupt()` (`controller/cli.py:1585-1609`),
writing the run's `interrupted` state and its `run_interrupted` event.

The consequence, intended: an interrupted waiting step writes **no job record**. A step's only job
record is written at its end (E.2), and a waiting step has launched no worker, so there is nothing
for a job record to evidence that the binding does not already hold. What evidences the wait is
the run log (`waiting` events and `run_interrupted`) and the binding's own events and record, each
written atomically as it happened. The next `step` or `run` re-reads and continues from the last
written binding state (C.3's intent row covers the one forge write).

**E.4 The lock.** The wait runs inside the step, under the target lock the step already holds; no
other Controller process can act on the target meanwhile, which is the intended exclusivity. A
second `step`/`run` meanwhile gets `LifecycleWorkerActiveError` (exit 45), whose message speaks of
a lifecycle worker; CP6 documents in `docs/guide/troubleshooting.md` that a waiting `run` holds the
same lock and how to tell (`status` shows the open run and its `waiting` event). The message itself
is unchanged.

### F. What the operator sees (CP3-CP5)

- **Gates.** `decision.BRANCH_GATE_TEXTS` (and the squash variants) gain `merge_pending`,
  `merge_held`, `release_pending` and `release_failed`; `GATE_CODES` gains them.
  `checks_failing`'s and `checks_cancelled`'s texts gain, for a `READY` record of an auto-merge
  binding only, that the Controller merges once a re-run turns the checks green (the next step);
  `post_acceptance_commits` gains C.4's `READY` text (same code).
- **`explain`** (`predict`, no network): a `READY` record with auto-merge applying predicts
  `merge` (no `merge` field yet, or `sending`) or `wait_merge` (`accepted`, "as of" the last
  observation); a `MERGED_SQUASHED` record with the release wait applying and no `release` predicts
  `wait_release`; after it, `close_out` names the stop.
- **`status`** binding lines add `merge: <state> at <head> (attempt <n>)` and
  `release: <tag> <url>` (or the settled state) when present; `status --json` `bindings` entries
  add `merge` and `release`. `tests/test_hints_parse.py`'s `SAMPLES` cover any new printed
  `workflow-controller ...` hint.

### G. Documentation and full verification (CP6, terminal)

- `docs/adr/0009-auto-merge-and-release-wait.md`: the decision, the narrowed I3', the two switches,
  the stop after close-out, and what stays human.
- `docs/guide/milestone-branches.md`: an "Auto-merge and the release wait" section, including
  that the merge happens branch-side only, so an unattended `run` started from the trunk stops at
  the open pull request's "switch to it" refusal (C.1); the flow diagram's squash line; the
  readiness gate table's new rows; "Merge and close-out".
- `docs/guide/ci-and-releases.md`: "Repository settings" (squash merging must be allowed; the
  Controller merges an accepted pull request when the policy opts in, and never uses GitHub's
  auto-merge request; `release_workflow`; merge-queue repositories are unsupported), and how
  `release_failed` is resolved.
- `docs/guide/automation.md`: "Never merges, never rewrites" restated as I3'.
- `docs/guide/runtime.md`: the three settings rows and the boolean type.
- `docs/guide/commands.md` and `docs/guide/troubleshooting.md` where they list gates or status
  lines.
- `docs/README.md` (the ADR row) and the 1.6.0 notes in the milestone narrative's
  `## Release notes` section, wrapped at 72 bytes and checked with
  `release_notes.notes_problem`.
- The full suite, the goldens with `--check` (including
  `generate_plan_stage_decisions.py --release 2.6.0 --check`), and the protected-path diff from
  the base.

## Checkpoints

The registry (`docs/ai-workflow/registry/workflow-controller-auto-merge-release-wait-registry.json`)
is the authority; this table is generated from it.

<!-- registry table: generated by workflow_state.render_registry_markdown, never hand-edited -->

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The two switches: repo_policy MilestoneBranches.auto_merge (default false; true requires merge_method squash and ready_requires_green_checks) and release_workflow (default main.yml), settings bool type and merge.auto/merge.wait_seconds/merge.poll_seconds rows with TABLE_GENERATION 2, Context fields wired from cli._effective through job.execute_step, auto_merge_applies/release_wait_applies over the binding snapshot; test_repo_policy and test_settings cases, the no-policy golden regenerated with only the three merge.* keys added to each job record's controller_settings | - | 2 | 1 |
| CP2 | The forge surface: PullRequest.merge_state, Run and commit_runs (gh run list --commit --branch --event push --workflow, full-page refusal), merge_squash (gh pr merge --squash --match-head-commit --subject --body, never --auto); tests/fake_gh.py models it with read lag and refuses --auto/--disable-auto; the never-merge static guard narrowed to exactly that argv shape in controller/forge.py with synthetic-source tests both ways; exact-argv tests with a run-only spy | - | 2 | 1 |
| CP3 | The merge at readiness: milestone_branch._merge_step replacing both _merge_gate returns when auto-merge applies, the first-match re-read table after the READY cell's own order (merged continues, closed, a remote-only push refused by _observe_branch, a local tip past A gates post_acceptance_commits with a READY text, a lagging PR head gates pr_head_not_accepted, draft gates merge_held, readiness's _checks_gate reused for failing/pending/cancelled checks, DIRTY/unknown or blocked states gate merge_pending, BEHIND gates integration_required; nothing sent on any of them), the head-bound merge_squash send with its sending/accepted intent record, crash rows and three-attempt refusal, merge.auto false byte-identical to 1.5.0, gate texts and GATE_CODES, predict merge/wait_merge rows; test_pull_request_lifecycle cases including each way the head can move after acceptance and a cancelled check | CP1, CP2 | 3 | 1 |
| CP4 | The release wait, close out and stop: _release_wait before close-out step 3 on the branch and trunk sides, classify of the squash commit with the policy committed at it and verify_assets=False (no asset download, no policy command), settled/pending/failed rows over the publishing workflow's runs only (commit_runs with release_workflow) the re-classify race and the superseded rows (a covering release at a trunk descendant settles SUPERSEDED) and the later-run recovery (any release_failed from m's or a covering tag's finished runs stays release_pending while the trunk tip's own publishing run can still publish), release_pending and release_failed gates, the release and merge record fields and binding events, Proceed.stop and the no-action outcome in job.execute_step (no /milestone-plan launched after an auto-merged close-out, no job record), predict wait_release; tests including a failed unrelated run before a delayed publishing run, m's pending run cancelled by a later push, a failed run of m or an unpublished covering tag recovered by a later trunk run, and the unchanged close-out for bindings without the key | CP3 | 3 | 1 |
| CP5 | The bounded wait in run, and status: waiting_preflight over the waitable gates, execute_step's wait_seconds/on_wait keywords set by cli from the subcommand with one budget per step, injected sleep and clock, waiting run events, step and merge.wait_seconds 0 never sleeping, Ctrl-C during the sleep via the existing SIGINT harness; status binding lines, inspect and status --json merge/release additions with the pinned key sets additions-only and the no-policy golden as CP1 regenerated it; an end-to-end run from checks_pending to closed out and stopped in one step with no job record | CP4 | 2 | 1 |
| CP6 | Documentation and full verification (terminal): ADR 0009, docs/guide/milestone-branches.md, ci-and-releases.md, automation.md (I3 narrowed), runtime.md settings rows, commands.md and troubleshooting.md, docs/README.md, the 1.6.0 notes section checked by release_notes.notes_problem, the hints SAMPLES, every golden generator with --check, the full suite, and the protected-path diff from the base | CP1, CP2, CP3, CP4, CP5 | 2 | 1 |

### CP1 -- the two switches

`repo_policy`: `MilestoneBranches.auto_merge` and `release_workflow`, parsing and the
admissibility rules (A.1).
`settings`: the `bool` type and the three `merge.*` rows, `TABLE_GENERATION` 2 (A.2); `Context`
fields and their wiring from `cli._effective` through `job.execute_step` (A.2), with
`auto_merge_applies`/`release_wait_applies` (A.3). Tests: `test_repo_policy` (absent → false;
true with squash and green checks admitted; true with merge mode or without green checks refused,
naming the field; a non-boolean refused; `release_workflow` absent → `"main.yml"`, a name with a
`/` or empty refused), `test_settings` (the bool type both ways; fill from a
generation-1 file adds the three rows and moves nothing; `clean`; `show`), the predicates over a
snapshot with and without the key. The existing policy tests unchanged. The no-policy golden is
regenerated here (I1, revision 6), and a test pins that its diff from the base is exactly the three
`merge.*` keys added to each job record's `controller_settings.sources` and `.values`, and nothing
else.

### CP2 -- the forge surface

`forge`: `PullRequest.merge_state`, `Run`, `commit_runs`, `merge_squash` (B.1, B.2);
`tests/fake_gh.py` (B.4); the narrowed static guard and its synthetic-source tests (B.3). Tests in
`test_forge` pin each argv exactly (a run-only spy, not the Popen-double-counting one), the JSON
parsing (an unknown `mergeStateStatus` passed through), `commit_runs`'s `--workflow` argument and
full-page refusal, and the fake's head-mismatch refusal and its refusal of `--auto`/`--disable-auto`.

### CP3 -- the merge at readiness

`milestone_branch._merge_step` and C.2-C.6; the `merge_pending` and `merge_held` gates and texts;
the `checks_failing`/`post_acceptance_commits` clauses; `predict`'s `merge` and `wait_merge` rows.
Tests (`test_pull_request_lifecycle`, fake `gh`): the merge sent at readiness with the exact argv
(no `--auto`) and continuing into `MERGED_SQUASHED`; each C.2 row in order, including the
first-match cases (a local commit past `A` with a failing check → `post_acceptance_commits`; draft
with pending checks → `merge_held`); **a push to the branch on GitHub only after acceptance while
checks are pending, then the checks completing green: `_observe_branch`'s "Update branch" refusal,
nothing is merged, and the fake records no merge call** (the external review's missing test,
revision 5, corrected in revision 6); a local commit pushed past `A` → `post_acceptance_commits`
with the `READY` text and no merge call; a local commit not pushed (the pull request still at `A`)
→ the same gate and no merge call; the pull request's head lagging at a tip and remote branch of
`A` → `pr_head_not_accepted`, no call; a cancelled check with a `CLEAN` merge state →
`checks_cancelled` and no merge call (revision 6); a pending check → `checks_pending`; an
`accepted` record's text naming the hand-merge and `milestone-binding` exits; a draft → `merge_held` with no
merge call, and marking it ready → the next step merges; `UNKNOWN`, a stale `DRAFT`, `BLOCKED` and
`UNSTABLE` → `merge_pending` with no call (the `BLOCKED`-with-green-checks text naming a requirement
other than the checks); `DIRTY` → `merge_pending` at once; `BEHIND` → `integration_required` at
once; a moved trunk without `BEHIND` at `CLEAN` → merged; a merge that succeeds but reads `OPEN` →
`accepted` + `merge_pending`, never re-sent, and the next step adopts `MERGED`; the crash between
intent and call (the next step re-reads and sends once) and between call and outcome (the next step
re-reads `MERGED`, or re-sends and GitHub's "already merged" refusal is adopted once visible); a
refused merge below three attempts → `merge_pending` naming the message, sent again only at
`CLEAN`; three refusals → the refusal, and a merge written to the fake closes it out; a person's
own auto-merge request (written into the fake) is neither read nor withdrawn; `merge.auto: false` →
today's `merge_pull_request` byte-for-byte; a binding without the key → today's behaviour for every
existing test.

### CP4 -- the release wait, close out and stop

`_release_wait` on both sides (D.1-D.3); `classify`'s `verify_assets` keyword, with a test that
the release wait downloads no asset and runs no policy command (a recording command runner and
forge), and `test_release_txn` unchanged for the default; the `release_pending`/`release_failed`
gates; the record fields and events (D.5); `Proceed.stop` and the no-action outcome in `job.execute_step` (D.4);
`predict`'s `wait_release` row. Tests (real Git test repositories via `fixtures.git_init`, a fake
forge and a fake classification seam where a real tag set is impractical): each D.2 row, including
`ALREADY_RELEASED` recording tag, version and URL; `NO_CHANGE` settling; the runs-pending and
runs-completed-but-unpublished paths, including the re-classify race; **a failed run of another
workflow listed before a delayed, successful publishing run** (the fake lists a failed `Workflow
conformance` run and no `Main` run, then a pending one, then a successful one with the release
published): `release_pending` throughout, never `release_failed`, then `released` (the external
review's missing test, revision 5); `commit_runs` called with the snapshot's `release_workflow`; a
failed publishing run → `release_failed` naming it; a successful publishing run with the commit still
unpublished → `release_failed` with the published-nothing text; a non-success
classification; **`m`'s pending publishing run cancelled by a later push whose run publishes the
covering release at the descendant** (revision 6): `release_pending` while the descendant's run is
running (and before it is listed), then settled `SUPERSEDED` with the descendant's tag and URL,
never `release_failed`; the same with the descendant raising the version further (`m` classifies
`RELEASE_DUE`); **a failed run of `m` followed by a later trunk push whose run is in progress and
then publishes the covering release at the descendant** (revision 7, the external review's missing
test): `release_pending` naming the failed run and the running one, never `release_failed`, then
`SUPERSEDED`; the same where the later run resumes `m`'s own tag (`m`'s run failed after tagging)
→ `released`; **a covering tag left unpublished by its failed run, then a newer trunk run that
resumes and publishes it** (the second missing test): `release_pending` while the newer run works,
then `SUPERSEDED`; the later run completing without publishing → `release_failed` naming both runs;
a failed run of `m` with the trunk tip still `m` → `release_failed` at once;
`COLLISION_TAG_ELSEWHERE` at a non-descendant → `release_failed`; a `cancelled`
run of `m` with no later trunk commit → `release_failed`; the trunk-side close-out after a manual `git switch main`; the stop (no
`/milestone-plan` launched, exit 0, the next step plans with the explicit base); a binding without
the key closing out and launching `/milestone-plan` exactly as today; an auto-merge binding merged
by hand with a merge commit (`MERGED`: no release wait, `release.state == "SKIPPED"`, event
`release_wait_skipped`, `/milestone-plan` launched as in 1.5.0) and one ending `MERGED_REWRITTEN`
(no release wait, the existing one-time gate, no close-out); `classify` called with the
preflight's `git_runner`; the all-runs-succeeded-but-unpublished `release_failed` wording.

### CP5 -- the bounded wait in `run`, and `status`

`waiting_preflight` (E.1-E.4) with an injected sleep and clock, and `execute_step`'s
`wait_seconds`/`on_wait` keywords set by `cli` from the subcommand (E.2);
`status`/`inspect`/`status --json`
fields (F). Tests: a `run` that waits through `checks_pending` → `READY` → `merge_pending` →
merged → `release_pending` → released → closed out and stopped, in one step, with no job record (the
no-action stop, D.4) and no worker launched; the budget expiring returns the last gate and writes
its job record; `merge.wait_seconds: 0` and
`step` never sleep; a non-waitable gate returns at once; the `waiting` events; Ctrl-C during the
sleep (a subprocess test with the existing SIGINT harness, not a backgrounded `&` job): the run
ends `interrupted` with `run_interrupted`, no job record is written for the waiting step, and the
next `step` continues from the last written binding state; the status
lines and JSON additions, and the pinned key sets that a new binding field touches (for example
`test_trunk_preflight` `ObservationTest` and the observation-equivalence normalisation) --
additions only. No job-record field is added, so the no-policy golden stays as CP1 regenerated it
(its `--check` passes unchanged in CP5, I1).

### CP6 -- documentation and full verification (terminal)

Design G. The full suite under the reaping-subreaper wrapper, every golden generator with
`--check`, and a protected-path diff from `854d25c` showing no change to the policy,
`pyproject.toml`, `setup.py` or `.github/workflows/`.

## Requirements

| Id | Requirement | Checkpoints |
|---|---|---|
| R1 | A repository opts in to auto-merge through `milestone_branches.pull_request.auto_merge`, admitted only with squash merges and green-check readiness, and names its publishing workflow (`release_workflow`); without it, 1.5.0's behaviour is unchanged | CP1 |
| R2 | The settings file can turn auto-merge off (`merge.auto`) and bounds the wait (`merge.wait_seconds`, `merge.poll_seconds`) | CP1, CP5 |
| R3 | The forge can squash-merge a pull request bound to a head commit and read one workflow's trunk runs of a commit; the never-merge guard is narrowed to exactly that shape | CP2 |
| R4 | A ready, accepted pull request is merged by a head-bound squash merge at the acceptance commit once GitHub reports it mergeable, crash-safely, and nothing left on GitHub can merge another head | CP3 |
| R5 | After acceptance, a red check, a draft hold, a conflict or a moved head stops and reports, without the Controller merging or overriding a person | CP3 |
| R6 | After the verified squash, the Controller waits for the publishing workflow's run, records the published release (its own, or a later run's release that covers it), or stops at `release_failed` naming the run | CP4 |
| R7 | Once the release is settled the milestone closes out and the run stops instead of planning the next milestone | CP4 |
| R8 | `run` waits through the pending states, boundedly and interruptibly, without a worker; `step` never waits | CP5 |
| R9 | `explain`, `status` and `inspect` show the merge and release states | CP3, CP4, CP5 |
| R10 | The guides, an ADR and the 1.6.0 notes describe the new behaviour; the full suite and goldens pass | CP6 |

## Decisions for the reviewer and the user

1. **Two switches, and what each controls.** The repository policy is the opt-in (default off),
   because ROADMAP 7.6 requires an explicit repository policy before any auto-merge and because the
   Workflow Manager lane runs the same install. The settings file (`merge.auto`, default on) can
   only turn the GitHub write off; the release wait and the stop follow the policy, because they
   only read. Alternative: one switch in the settings file only, as 11.3's last bullet literally
   says ("The settings file (C3) can turn auto-merge off"); rejected because a user-level file is
   not a repository decision.
2. **Close out, then stop.** 11.3 says the Controller "closes out, and stops", and C11 adds the
   loop to the next item. Today a close-out step launches the next `/milestone-plan` at once. This
   plan stops after an auto-merged milestone's close-out only; a binding without the key keeps
   today's behaviour. The pause also leaves room for the release-notes docs pull request before the
   next milestone takes `main`'s tip as its base. If you prefer today's "continue into the next
   plan", D.4 drops the stop and nothing else changes.
3. **Only `run` waits, up to an hour by default.** `step` stays "one action, then return".
   `merge.wait_seconds` defaults to 3600 (this repository's PR checks and `Main` run each take
   minutes, not hours); `0` restores the gate-at-once behaviour.
4. **Explicit squash subject and body.** The merge carries `<title> (#<n>)` and the pull request's
   body, so the squash message does not depend on the repository's default squash-message setting.
   A title edited on GitHub between the Controller's re-read and its merge call would be overwritten
   by the subject sent; one edited before is the title sent.
5. **A red check after acceptance stops the merge** (revised in revision 5). Nothing is pending on
   GitHub, so a green re-run ("Re-run failed jobs", which C2 made count) is merged by the next
   Controller step, not by GitHub alone. `checks_failing` is not waitable: a red check needs a
   person.
6. **No standing request, and a person's hold is respected** (revised in revisions 5 and 6): a
   moved head ends at `_observe_branch`'s refusal or `post_acceptance_commits`, with nothing sent and
   nothing to withdraw (C.4); a draft holds the merge (C.5).
7. **No release retries.** `release_failed` stops; re-running the workflow or publishing by hand is
   a person's call until C10/C11.
8. **A hand merge that is not the verified squash skips the release wait and the stop** (revision
   2, D.1's table): `MERGED` closes out as 1.5.0 does; `MERGED_REWRITTEN` keeps its terminal gate.
9. **A head-bound merge by the Controller, not GitHub's auto-merge request** (revision 5). ROADMAP
   11.3's first bullet says the Controller "enables GitHub auto-merge, and GitHub merges when the
   required checks are green". This plan deviates: a request stays enabled after a push by someone
   with write access and GitHub then merges a head nobody accepted, before any poll can react
   (investigation). The Controller instead sends `mergePullRequest` bound to `A` whenever its own
   re-read shows the pull request mergeable. The cost: the merge happens only while a Controller
   step runs -- a `run` waiting (E) merges within one poll of the checks turning green; without a
   running Controller the pull request waits for the next `run`. The roadmap row's wording is
   updated in the post-release docs pull request. Alternative rejected: keep the request and poll
   it -- the window between a push and the next poll cannot be closed.

### Revision 2: local plan review round 1

All five important findings and all four optional findings were accepted; none was rejected.

- **Important 1** (the three-attempt refusal's exit could not clear it): C.2 now names only exits a
  re-read adopts without a send (merge on GitHub; enable auto-merge on GitHub yourself), and
  re-sends only after `REQUEST_VISIBILITY_SECONDS` since the last attempt, so the budget is
  independent of `merge.poll_seconds`. Tests added to CP3.
- **Important 2** (`--auto` on a mergeable pull request): settled from gh 2.101.0's source
  (`merge.go` `isImmediatelyMergeable`, `http.go` `mergePullRequest`): it merges at once, bound by
  `expectedHeadOid`. The investigation, C.2 and I3' say so; the open question is closed.
- **Important 3** (head moved / disable): C.4 disables only when GitHub shows a request
  (`withdrawn` vs `disabled_observed`), the head check runs before any re-send of a `requested`
  record, and C.3 is stated as first-match. Tests added to CP3.
- **Important 4** (non-squash merges of an auto-merge binding): D.1's table and Decision 8; D.4's
  stop keys on the release wait having run. Tests added to CP4.
- **Important 5** (no existing interruptible wait): E.3 rewritten to the actual mechanism
  (`KeyboardInterrupt` → `cli.main` → `run.interrupt()`), and the no-job-record consequence stated
  as intended. Test added to CP5.
- **Optional 1** E.4 + troubleshooting; **Optional 2** `git_runner=ctx.runner` in D.2;
  **Optional 3** D.3's unpublished-without-failure wording; **Optional 4** E.2's rate arithmetic.
- **Architecture concern** (pin the guard to forge's argv literal, refuse a built `"merge"`): B.3.
- The checkpoint set, names, dependencies and requirements are unchanged; the registry and mapping
  are regenerated at revision 2 only.

### Revision 3: local plan review round 2

- **Important 1** (`UNKNOWN` turns the usual path into a refusal): accepted, options (a) and (b).
  C.2 sends nothing while the merge state is not computed (`UNKNOWN`, or a stale `DRAFT`), and a
  step-2 `ForgeError` is classified by a re-read rather than by its text: a clean re-read below
  the attempt budget is the waitable `merge_pending` and re-sends at the next poll; anything else
  still refuses (fail-closed). The investigation, E.1 and CP3's tests say so.
- **Important 2** (`BEHIND` waits forever): accepted for `BEHIND` -- a C.3 row gates
  `integration_required` at once, auto-merge left enabled, not waitable (E.1), with a CP3 test.
  **Rejected in part:** the suggested second trigger, "the fetched trunk is no longer an ancestor
  of `A`", is not added. Without `BEHIND` GitHub still merges (the repository does not require
  up-to-date branches), and the merge verifies: `verified_squash` handles a squash parent that is
  not an ancestor of `A` with `git merge-tree` (`controller/milestone_branch.py:999-1011`). Gating
  there would stop a merge GitHub is about to perform. A CP3 test pins that a moved trunk without
  `BEHIND` stays `merge_pending`.
- **Optional 1** (merge queues): accepted as "unsupported", in Non-goals, with the fail-closed path
  (`MERGED_REWRITTEN`) and a guide note in CP6. No `isMergeQueueEnabled` read is added.
- **Optional 2** (zero runs): `release_pending`'s message names the hand-publication exit (D.3).
- **Optional 3** (B.3 wording): B.3 now says the concatenation detection is new and CP2 writes it.
- The checkpoint set, names, dependencies and requirements are unchanged; the registry and mapping
  are regenerated at revision 3 only.

### Revision 4: local plan review round 3

All findings were accepted; none was rejected.

- **Important 1** (the immediate re-send after a stale-state rejection had no record state to key
  on): option (b), a stateless rule. A `requested` record whose re-read shows `OPEN` at `A`, no
  request and a `CLEAN`/`HAS_HOOKS`/`UNSTABLE` merge state re-sends at once (attempts permitting);
  only other states wait `REQUEST_VISIBILITY_SECONDS`. No record field is added. It is safe under
  I2 because gh sends the head-bound direct merge on those states, never a second enable. The cost,
  stated: a direct merge GitHub has made but still reads as `OPEN` can spend the remaining attempts
  within a few polls and refuse; the refusal's first exit ("merge on GitHub") is then already
  satisfied, and the next step's re-read adopts `MERGED`. CP3 adds the crash-between-rejection-and-
  outcome case.
- **Optional 1** (`BLOCKED` with green checks): C.3's `merge_pending` text names a requirement other
  than the checks; still waitable. CP3 test.
- **Optional 2** (partial run listing): the second suggested fix -- all completed and successful but
  unpublished stays `release_pending` for `RELEASE_REGISTRATION_SECONDS` after `m`'s committer time,
  then `release_failed`. A run that did not succeed still gates `release_failed` at once. CP4 test.
- **Optional 3** (`DIRTY` at send time): a step-2 re-read showing `DIRTY` or `BEHIND` routes to
  C.3's rows, and the requested-record rule sends nothing while either lasts. CP3 tests.
- The checkpoint set, names, dependencies and requirements are unchanged; the registry and mapping
  are regenerated at revision 4 only.

### Revision 5: manual external (Codex) plan review round 1

All three important findings and the optional finding were accepted; none was rejected.

- **Important 1** (a pending auto-merge request can merge a head newer than `A`): accepted. It holds
  on GitHub's own documented behaviour: `expectedHeadOid` binds only the enable call, and a push by
  someone with write access leaves the request enabled, so GitHub can merge the new head before any
  poll. No polling design closes that window, so revision 5 never enables a request: the Controller
  sends a `mergePullRequest` bound to `A` (`gh pr merge --squash --match-head-commit`, no `--auto`;
  gh's source shows `autoMerge` is false without `--auto`) whenever its own re-read shows `A`, green
  checks and `CLEAN`/`HAS_HOOKS` (C rewritten; I2, I3', B.2-B.4, F, Decisions 5, 6 and the new 9).
  This departs from ROADMAP 11.3's "enables GitHub auto-merge" wording, which Decision 9 puts to
  the user. CP3 adds the external review's push-after-acceptance test.
- **Important 2** (the `requested` recovery rule could re-enable a request a person withdrew):
  accepted, and resolved by the same change: there is no request to re-enable. Every send is a
  one-shot head-bound merge, so a re-send after a crash or an ambiguous result is either the merge or
  refused, and nothing outlives the step. A person's hold is a draft (`merge_held`, C.5) or
  `merge.auto: false`; the Controller never sends while the pull request is a draft. A request a
  person enables on GitHub themselves is neither read nor withdrawn. CP3 tests both.
- **Important 3** (an unrelated run can produce a false `release_failed`): accepted. D.3 now reads
  only the publishing workflow's runs (`gh run list --workflow <release_workflow>`, a new optional
  policy key defaulting to `"main.yml"`, A.1), keeps every incomplete or unlisted publishing run
  `release_pending` with no time-based failure (the wait budget is the bound), and fails only on a
  completed publishing run whose conclusion is not `success`, or a successful one that published
  nothing. `RELEASE_REGISTRATION_SECONDS` is removed. CP4 adds the external review's
  failed-unrelated-run-before-delayed-`Main` test.
- **Optional 1** (the no-action close-out writes no job record): accepted; confirmed at
  `controller/job.py` `_execute_step_locked`'s docstring ("no job record is written at all" for the
  no-action class). D.4, E.2 and CP5 now say the stop writes no job record, and CP5's end-to-end
  test expects none.
- The checkpoint set, ids, dependencies and complexity are unchanged; CP1-CP4's names and R1, R3-R6
  and R9 are reworded to the new design; the registry and mapping are regenerated at revision 5.

### Revision 6: local plan review round 5

All four important findings and all three optional findings were accepted; none was rejected.

- **Important 1** (the no-policy golden changes): accepted. Confirmed: each job record in
  `tests/golden/no_policy_lifecycle.json` carries `controller_settings` with one `sources` and one
  `values` entry per `settings.TABLE` key. I1 is now behavioural and names that additive change;
  CP1 regenerates the golden and pins the diff; CP5 and the CP1/CP5 registry names agree.
- **Important 2** (the head-moved row described behaviour 1.5.0 does not have): accepted. Confirmed
  at `controller/milestone_branch.py:673-683` (the `READY` cell returns `_merge_gate` with no head
  check) and `:733-739` (`_observe_branch`'s refusal for a remote branch that is not an ancestor of
  the local tip). C.2 now lists the cell's own order; C.4 defines the remote-only push (the
  existing refusal), the local-and-remote push and the local-only commit (`post_acceptance_commits`
  with a `READY` text), and a lagging pull-request head (`pr_head_not_accepted`). CP3's
  push-after-acceptance test expects the refusal, with new cases for the other two.
- **Important 3** (no cancelled-checks row): accepted, with the architecture concern: C.2 calls
  readiness's `_checks_gate` (`milestone_branch.py:1504-1526`) and returns its gate unchanged, so a
  pending check is now `checks_pending` (waitable) rather than a `merge_pending` variant, and a
  cancelled one `checks_cancelled` (not waitable). E.1, F and CP3 updated.
- **Important 4** (a superseded publishing run gives a false `release_failed`): accepted. Confirmed
  at `.github/workflows/main.yml:28-30` and `controller/release_txn.py:510-512`. The investigation
  is corrected; D.2 routes `COLLISION_TAG_ELSEWHERE` at a descendant to D.3's new superseded rows
  (covering tag published → `SUPERSEDED`; a cancelled run of `m` with a later trunk run still
  working → `release_pending`). CP4 tests both, and the higher-version variant.
- **Optional 1** (`accepted` with no exit): its text names the hand merge and `milestone-binding`.
- **Optional 2** (the `run`/`step` seam): E.2 names `execute_step`'s `wait_seconds` and `on_wait`
  keywords and where `waiting` is emitted.
- **Optional 3** (trunk-side `READY`): C.1 and CP6's guide work say an unattended `run` from the
  trunk stops at the existing refusal.
- The checkpoint set, ids, dependencies and complexity are unchanged; CP1, CP3, CP4 and CP5's names
  are reworded; the registry and mapping are regenerated at revision 6.

### Revision 7: manual external (Codex) plan review round 2

The one important finding was accepted; there were no optional findings.

- **Important 1** (a failed publishing run stops the wait while a later run is publishing):
  accepted. Confirmed: revision 6's descendant-run row applied only when every run of `m` was
  `cancelled`, and the covering-tag row failed on `t`'s completed runs; yet `main.yml` at a later
  trunk commit tags it (`RELEASE_DUE`) or resumes the existing tag (`RESUME`,
  `controller/release_txn.py:546-548`) whatever the earlier runs' conclusions. D.3's new "Recovery
  by a later trunk run" applies to every `release_failed` D.3 gives, from `m`'s runs or a covering
  tag's, and keeps `release_pending` while the trunk tip's run can still publish; failure is
  reported only once that run has finished without publishing. It subsumes the cancelled-only row.
  CP4 adds the review's two missing tests, plus the resume-at-`m` and tip-still-`m` variants.
- The checkpoint set, ids and dependencies are unchanged; CP4's name is reworded; the registry and
  mapping are regenerated at revision 7.

## Open questions

- None. (Functional review records gh's and GitHub's exact merge-refusal texts, for the guide
  only.) Revision 5 no longer sends `--auto`, so revision 1's question (whether it merges at once on
  a mergeable pull request) no longer bears on the design; the gh source reading that settled it
  still shows that without `--auto` gh never enables a request (investigation).

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-auto-merge-release-wait-artifacts.json` follows the
settings-and-telemetry declaration, with this item's paths:

- plan stage: protected are this plan, its registry and its mapping; `controller/`, `tests/`,
  `tools/`, `docs/guide/`, `docs/adr/`, `docs/releases/`, `.workflow-controller/`, `docs/README.md`,
  `pyproject.toml` and `setup.py` are excluded as implementation content (the policy as content
  this milestone leaves unchanged);
- implementation stage: protected are `controller/`, `tests/`, `tools/`, `docs/guide/`,
  `docs/adr/`, `docs/releases/`, `.workflow-controller/`, `.github/workflows/*.yml`,
  `docs/README.md`, `README.md`, `CLAUDE.md`, `pyproject.toml`, `setup.py` and this artifacts file
  itself; the plan, registry and mapping are excluded as plan-stage content, and the workflow's
  own bookkeeping (`WORKFLOW_STATE.json`, `docs/ACTIVE_MILESTONE.md`, `docs/ROADMAP.md`,
  `docs/milestones/`) is excluded.

## Verification

- CP1-CP5: each checkpoint's own tests plus the full suite (`python3 tools/run_tests.py` under the
  reaping-subreaper wrapper; no `PYTHONPATH=.`, no `FORCE_COLOR`).
- Goldens: `tests/golden/generate_*.py --check` (never `--help`), including
  `generate_plan_stage_decisions.py --release 2.6.0 --check`.
- CP6: the protected-path diff from the base, and `release_notes.notes_problem` on the narrative's
  notes section.
- Functional review (after technical approval): the merge and the release wait in a disposable
  GitHub-free setting (fake `gh`) plus one live `gh pr merge --squash --match-head-commit` on a
  throwaway pull request of this repository, merged as a `chore:` no-release change or refused at a
  wrong head (the user decides at the checklist).

## Migration / data-integrity notes

- Records and policies written by 1.5.0 are read unchanged; the new record fields are additions
  and absent on old records (I1). A binding bound before the opt-in never auto-merges (I8).
- The settings file gains three rows through the existing additive fill; a 1.5.0 Controller
  sharing the file ignores them with its unknown-key warning and never removes them (`clean`
  refuses a newer table generation).
- **Order of the opt-in.** 1.6.0 is released, then installed into the shared install between
  Workflow Manager milestones (the shared-lane rule), and only then a `chore:` pull request adds
  `"auto_merge": true` to this repository's policy (Controller 1.5.x refuses the key). The
  repository allows squash merges already; GitHub's "Allow auto-merge" setting is not used. The
  Workflow Manager repository opts in, if ever, through its own policy.
- Rolling back to 1.5.x after the opt-in: 1.5.x refuses the policy; revert the `chore:` pull
  request first.
