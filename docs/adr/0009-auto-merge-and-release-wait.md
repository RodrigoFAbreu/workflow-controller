# ADR 0009: Auto-merge after acceptance, and the wait for the release

Status: accepted (2026-10-02). See
`docs/ai-workflow/CONTROLLER_AUTO_MERGE_RELEASE_WAIT_PLAN.md` for the full
design record (work item `workflow-controller-auto-merge-release-wait`,
`docs/ROADMAP.md` step C4, section 11.3). The code ships in 1.6.0. This
document records when the Controller merges a milestone pull request, how,
what it waits for afterwards, and what stays with a person. It adds no exit
code: the merge's refusal is a `BranchBindingError` and exits 20, and the
new gates exit 10 like every gate
([ADR 0001](0001-controller-generation-1-architecture.md)). The operator's
view is in
[the milestone branch guide](../guide/milestone-branches.md#auto-merge-and-the-release-wait).

## Context

Through 1.5.0, after `/accept-milestone` the Controller marked the pull
request ready and stopped at `merge_pull_request`. A person then pressed
"Squash and merge", watched the `Main` run publish the release, and ran the
Controller again to close out, although nothing was left to decide.
"The Controller never merges" was a tested invariant
(`tests/test_no_rewrite_invariants.py`, the trunk plan's I3), and
`docs/ROADMAP.md` 7.6 allowed an auto-merge only through an explicit
repository policy. Both lanes run the same shared install, so any change
had to leave a repository that does not opt in exactly as it was.

## Decisions

### Two switches

- **The repository opts in.** `milestone_branches.pull_request.auto_merge`
  (default `false`) is the opt-in. `true` requires `merge_method: "squash"`
  and `ready_requires_green_checks: true`, or the policy is refused: the
  squash verification and the release wait are squash-mode only, and the
  Controller must have seen green checks before it merges.
  `release_workflow` (default `main.yml`) names the workflow whose trunk
  push run publishes the release. Both are read from the binding's policy
  snapshot, so a policy edit never changes a milestone in flight, and a
  binding made before the opt-in never auto-merges.
- **The operator can turn it off.** The settings file's `merge.auto`
  (default `true`) turns off only the GitHub write: readiness then ends at
  `merge_pull_request`, as in 1.5.0. The release wait and the stop after
  close-out follow the policy alone, because they only read. A user-level
  file is not a repository decision, so it cannot turn the merge on.

### One head-bound merge, never GitHub's auto-merge request

The Controller merges only a `READY` record's pull request, only when its
own re-read in that step shows the local tip, the remote branch and the
pull request head all at the acceptance commit `A`, the pull request not a
draft, every check green, and GitHub's merge state `CLEAN` or `HAS_HOOKS`.
It then sends one `gh pr merge <n> --squash --match-head-commit A --subject
"<title> (#<n>)" --body <body>`, which is GitHub's `mergePullRequest` with
`expectedHeadOid`: GitHub merges exactly `A`, or refuses.

GitHub's auto-merge request (`--auto`) is never used. A request is checked
against `--match-head-commit` only when it is made; after a later push by
someone with write access it stays enabled, and GitHub merges a head nobody
accepted before any poll could react. A merge the Controller sends, by
contrast, leaves nothing behind that can merge later. The cost is that the
merge happens only while a Controller step runs.

The send is crash-safe: the intent (`merge.state: "sending"`, the head, the
attempt count) is written first, every step decides again from a fresh
re-read, and a duplicate of a merge that already happened is refused by
GitHub and adopted once the merge is visible. After three refused attempts
the Controller sends nothing more and refuses, naming GitHub's message; a
person merges.

### The invariant, narrowed (I3')

"The Controller never merges" becomes: the Controller merges only the
accepted head, only through GitHub, and never writes the trunk. The only
`gh pr merge` argv in `controller/` is one list literal in
`controller/forge.py` that carries `--squash` and `--match-head-commit` and
none of `--auto`, `--disable-auto`, `--admin` or `--delete-branch`;
`--auto` and `--disable-auto` appear nowhere. The static scan enforces this
shape, and also refuses a `"merge"` string built by concatenation or
formatting in `forge.py`. No code path pushes to the trunk ref, closes a
pull request or deletes a branch. The merge runs under the repository's
branch protection and required checks, as any GitHub merge does.

### A person's hold wins, and nothing after acceptance is guessed

A pull request converted back to a draft is not merged (`merge_held`). A
commit after `A`, pushed or not, stops the merge at
`post_acceptance_commits`; a push on GitHub only is refused as before;
failing or cancelled checks, a conflict (`DIRTY`) and a branch GitHub
requires to be up to date (`BEHIND`, the manual `integration_required`
procedure) each end the step at a gate that names the exit. The fix loop
for a red pull request after acceptance is later work (C10).

### The release is read, never made

After the verified squash (`MERGED_SQUASHED`) and before close-out step 3,
on the branch and the trunk side alike, the Controller classifies the
squash commit with the release transaction `main.yml` runs
(`release_txn.classify`), under the policy committed at that commit, with
asset verification off: no asset is downloaded and no policy command
runs on the operator's machine. CI's own `verify` job already checked what
it published. Only the runs of `release_workflow` for that commit decide a
due release: pending while one is missing or running, `release_failed`
once all completed without publishing. A later trunk run that published,
or can still publish, the release covering the squash commit settles it
(`SUPERSEDED`) or keeps it pending, because `main.yml` cancels a pending
run when a newer one queues, and any later run tags or resumes the
version. The Controller never builds, tags, publishes or re-runs.

### Close out, then stop

Once the release is settled, close-out runs unchanged, and the step ends
with a no-action outcome (`closed_out_released`, exit 0, no job record)
instead of launching the next `/milestone-plan` in the same run. The next
`run` plans from the trunk tip as before. The pause leaves room for the
release-notes pull request; continuing into the next plan is C11's loop. A
hand merge that is not the verified squash skips the release wait and the
stop: a merge commit closes out as in 1.5.0, a rewrite stays at its
terminal gate.

### `run` waits, boundedly; `step` never does

Inside `run`, a step whose outcome is a pending gate (checks, the pull
request head, the merge or the release pending) re-runs the full preflight
every `merge.poll_seconds` (10 to 600, default 30) for at most
`merge.wait_seconds` (0 to 86400, default 3600) per step, across gate
changes, without launching a worker and under the lock the step already
holds. Only the final outcome is recorded. Ctrl-C leaves the binding at its
last atomically written state and writes no job record for the waiting
step. `step`, `explain`, `status` and `inspect` never wait. The poll bound
keeps a waiting `run` well inside GitHub's API rate limits.

### What stays human

`/accept-milestone` stays the last human gate. Approvals, the fix loop
after a red check, integrating the trunk into a milestone branch, a
release that failed, a conflict, a hold, and every repository that does
not opt in remain a person's.

## Alternatives rejected

- **GitHub's auto-merge request, then polling it.** A request outlives its
  head check; the window between a push and the next poll cannot be closed.
- **One switch in the settings file only.** A user-level file shared by
  every repository on the machine is not a repository's decision, and
  ROADMAP 7.6 requires an explicit repository policy.
- **Waiting in `step` too.** `step` stays one action, then return.
- **Verifying the release's assets in the Controller.** It would download
  them and run repository code on the operator's machine from a checkout
  that need not be the classified commit.
- **Continuing into the next `/milestone-plan` after the close-out.** The
  roadmap asks the Controller to stop after close-out; the loop to the next
  item is later work.
- **Merge queues.** A queue writes its own commit message, so the squash
  cannot be verified; such repositories are unsupported and fail closed at
  `MERGED_REWRITTEN`.
