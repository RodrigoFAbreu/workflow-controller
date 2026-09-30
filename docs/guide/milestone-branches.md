# Milestone branches and pull requests

[Back to the documentation map](../README.md)

This behaviour is **off** unless the target commits
`.workflow-controller/policy.json` with `milestone_branches.enabled`. The
policy is read from the committed tree at `HEAD`, never from the working
tree, and an inadmissible policy (an unknown schema, key or adapter kind,
or an invalid value) refuses every lifecycle command rather than being
ignored. This repository's own policy is the reference configuration:
trunk `main` on `origin`, forge `github`, branches
`milestone/{work_item_id}`, Draft pull requests that become ready only
with green checks, merged with "Squash and merge" (from the
[cutover](ci-and-releases.md#cutover-from-the-version-file-model) on;
before it, with "Create a merge commit").

`milestone_branches.pull_request.merge_method` chooses how a milestone
pull request is merged: `"merge"` (the default) or `"squash"`. A policy
whose release trigger is `conventional_commit` must use `"squash"`. The
mode is read from the policy snapshot taken when the branch was bound,
so a milestone keeps the mode it started with. With the policy active,
one milestone runs like this (squash mode):

```text
main -- /milestone-plan <main tip> -- bind milestone/<id> -- plan reviews -- plan approval (on the branch)
     -- push, Draft PR titled from the plan -- implementation, reviews, acceptance (each step pushed)
     -- readiness: title and body synced, PR marked ready -- HUMAN merges ("Squash and merge")
     -- close-out: switch to main, fast-forward -- next /milestone-plan <main tip>
```

In merge mode the flow is the same, but the PR is titled with the
work-item id, readiness edits nothing, the human merges with "Create a
merge commit", and every text below that names "Squash and merge" names
"Create a merge commit" instead. Merge mode's titles, bodies, gates and
close-out are 1.3.0's.

- **Trunk start.** Before `/milestone-plan`, `HEAD` must be on
  `main`, the tracked tree clean, and local `main` equal to
  `origin/main`. A `main` behind the remote gates `fast_forward_trunk`
  (the Controller does not fast-forward at start: you may have local
  work), and a diverged one refuses. Once the trunk start passes, the
  Controller names the next command with an explicit base,
  `/milestone-plan <main tip>` (the full 40-hex commit, just proved equal
  to `origin/main`), so every milestone's `base_commit` is its branch
  point. This holds for both merge modes; after a squash it matters,
  because the previous milestone's completion commit is not on `main`.
  Without a policy, or with milestone branches off, the command stays a
  bare `/milestone-plan`.
- **Bind.** After `/milestone-plan` has written the plan, and before
  anything about it is committed, the Controller creates
  `milestone/<id>` at `main`'s tip and switches to it, carrying the
  uncommitted plan along. Workflow (2.5.1 and 2.6.0) derives the work-item
  id itself, so the branch cannot be created earlier. A plan approval
  commit that already sits on `main` refuses with manual-recovery guidance.
- **Draft PR.** As soon as the branch has a commit beyond `origin/main`
  (normally the plan approval commit), the Controller pushes it and
  opens one Draft PR, whose body carries a
  `<!-- workflow-controller: work_item=<id> -->` marker line. In merge
  mode it is titled with the work-item id. In squash mode it is titled
  with the plan's declared title, or the work-item id without a valid one
  (see [below](#squash-merges-and-the-pull-request-title)). Every later
  step first fast-forwards the remote branch to the local tip. A PR is
  the milestone's only if its number, head, base and repository match and
  it is not from a fork; zero matches create, exactly one open match is
  adopted, anything else refuses.
- **Drift.** Each step records whether `origin/main` is still an
  ancestor of the branch and how far behind the branch is. Before
  acceptance this is informational (`inspect`, `explain` and `status`
  show it).
- **Readiness.** Once the branch's own committed state records the work
  item `MILESTONE_COMPLETE`, the PR is marked ready only when the local
  and remote tips are exactly the acceptance commit (the
  `/accept-milestone` commit, `Workflow-Work-Item` trailer included),
  `origin/main` is an ancestor of it, and at least one check is reported
  for that head with every check passing or skipped. In squash mode the
  title and body are synced first, before the checks are read (see
  [below](#squash-merges-and-the-pull-request-title)). Otherwise the step
  gates:

  | Gate | Meaning | What you do |
  |---|---|---|
  | `checks_pending` | no check reported yet, or one still running; the common case just after the final push. In squash mode also right after readiness edited the title or body, whose checks then re-run | wait, then `run` again |
  | `checks_failing` | a check failed | if it is flaky, "Re-run failed jobs" on GitHub is enough, for a failed test and a leaked process alike: the failed shard's job is red, so it re-runs, and `tests-result` counts its latest attempt (see [Re-running failed jobs](ci-and-releases.md#re-running-failed-jobs)); a fix committed on the branch follows the acceptance commit, so readiness then gates `post_acceptance_commits` |
  | `checks_cancelled` | a check was cancelled, none pending or failing | re-run it on GitHub |
  | `post_acceptance_commits` | commits follow the acceptance commit | merge anyway on GitHub ("Squash and merge"), or leave the gate standing; the Controller never removes them |
  | `integration_required` | `main` moved after the branch point | the manual merge procedure below |
  | `pr_head_not_accepted` | GitHub does not show the pushed acceptance commit as the PR head yet | `run` again once it does |
  | `pr_title_invalid` | squash mode: the plan declares no valid title, and the PR's title is not a valid Conventional Commit | set a valid title on the PR on GitHub, then `run` again |
  | `merge_pull_request` | the PR is ready | merge it on GitHub with "Squash and merge", without editing the commit message |

- **Merge and close-out.** You merge; the Controller never does. The
  next step checks that the merged head contains the acceptance commit
  and is itself on `origin/main`, then (with a clean tree, and the
  branch tip equal to the merged head) switches to `main`, fast-forwards
  it and records the binding `CLOSED`. The usual manual path, "merge on
  GitHub, then `git switch main && git pull`", converges the same way
  from `main`. The Controller deletes neither branch. GitHub's "automatically
  delete head branches" setting is your choice; this repository has it on, and
  close-out still works because it fetches the merged head from
  `refs/pull/<n>/head`. Close-out gates `dirty_tree` on
  an unclean tree, and `unmerged_commits` on local branch commits that
  were never merged (move them onto a new branch, then
  `git reset --keep <merged-head>` on `milestone/<id>`). In squash mode a
  verified squash is recorded `MERGED_SQUASHED` and closes out the same
  way, with the squash commit standing in for the merged head (see
  [below](#squash-merges-and-the-pull-request-title)). Any other merge
  that takes the reviewed commits off `main` -- a rebase merge, or a
  squash in merge mode -- is recorded (`merge_method_rewrote_history`,
  shown once); the Controller does not switch, and blocks nothing
  afterwards. With `HEAD` still on a finished milestone's branch, every
  step gates `switch_to_trunk`.

**`integration_required` is the normal path, not an error.** Workflow
2.5.1 and 2.6.0 have no transition that moves a work item's base and
re-establishes review against it, so the Controller never integrates
`main` into a milestone branch. Whenever anything lands on `main` during a
milestone -- including this repository's own post-acceptance release commits -- the
milestone ends at `integration_required`, and the supported procedure is:
on GitHub, mark the PR ready and merge it with "Squash and merge" (in
merge mode, "Create a merge commit"). Close-out then converges as for any
merge. This stays the contract until
a Workflow release provides the narrow follow-up that
[ADR 0006](../adr/0006-workflow-release-admission-and-per-release-contracts.md#e1-e5-the-released-contract-has-no-integration-transition)
names (an integration record, a base-moving transition, merge-admitting
provenance, legal phases and a mergeable state model), and a Controller
milestone binds integration to it. This is why this repository's ruleset
does not require a pull request to be up to date before merging (see
[Repository settings](ci-and-releases.md#repository-settings)).

## Squash merges and the pull request title

In squash mode the pull request lands on `main` as one commit whose
subject is the PR's title, followed by ` (#<number>)`, and whose body is
the PR's description. Under the `conventional_commit` release trigger
that subject decides the release (see
[Releasing](ci-and-releases.md#the-pull-request-title-decides-the-release)),
so the title is reviewed with the plan.

**The plan declares the title.** A milestone plan carries exactly one
line starting with `Pull request title:`, in its header block, of this
form (the title in backticks):

```text
Pull request title: `feat: squash merges with release versions derived from pull request titles`
```

The Controller reads it from the plan committed at the branch's `HEAD`
(the plan path from the committed `WORKFLOW_STATE.json`, never the
working tree). The whole line must match the regular expression
``Pull request title: `(?P<title>[^`\r\n]+)` ``, so nothing may follow
the closing backtick; zero such lines, more than one, or a malformed one
declare no title. The title must also be a valid Conventional Commit:
under `conventional_commit`, against the `change_types` of the policy
committed at `HEAD`, the same rule as the `PR title` check; otherwise
against the grammar alone. Only a plan amendment changes a declared
title.

**Title and body sync.**

- At creation the Draft PR is titled with the declared title, or with the
  work-item id when there is none. The required `PR title` check then
  fails from the first push, while the plan can still be amended.
- Every later step before acceptance sets the PR's title back to the
  declared one when they differ. A readied PR is never edited.
- At readiness, after the head and freshness conditions and before the
  checks are read, the declared title wins over one a human set on
  GitHub. Without a declared title, a valid title already on the PR is
  kept; otherwise readiness gates `pr_title_invalid`. The plan can no
  longer be amended after acceptance, so the exit is to set a valid
  title on GitHub.
- The body becomes the squash commit's body. It is created as the first
  and last lines below, and readiness adds the middle one. No line in it
  parses as a Git trailer.

  ```text
  Milestone `<id>`, planned in `<plan path>`, driven by workflow-controller.
  Accepted at <acceptance commit> on `milestone/<id>`; merge with "Squash and merge".

  <!-- workflow-controller: work_item=<id> -->
  ```

- An edit at readiness ends the step at `checks_pending`: the title
  check re-runs, and the PR is marked ready only on a later step that
  finds nothing to edit.

**Merge with "Squash and merge", and do not edit the commit message**
in GitHub's merge dialog. The repository's default squash message
("Pull request title and description") is what close-out recognises.

**`MERGED_SQUASHED` and close-out.** The merged head is not on `main`
after a squash, so the next step verifies the merge before it records
`MERGED_SQUASHED`. All of these must hold:

1. the PR's merge commit is on `origin/main`, with exactly one parent;
2. its subject is exactly the PR's title followed by ` (#<number>)`;
3. it is not a rebase of the merged head: its author name, author email,
   author date and full message are not all equal to the head's;
4. its tree is the reviewed content: the merged head's tree when its
   parent is an ancestor of the head (`main` had not moved), or otherwise
   (the `integration_required` path) the tree
   `git merge-tree --write-tree` gives for its parent and the head.
   That path needs Git 2.38 or later; an older Git, or a failing
   `merge-tree`, refuses and writes nothing, and the next step after the
   fix verifies again. A configured `merge.<name>.driver` is never run:
   with one configured, the merge is not verified.

Anything else is recorded as a rewrite (`merge_method_rewrote_history`).
From `MERGED_SQUASHED` close-out runs as for a merge commit, from the
branch or from `main`, with the squash commit standing in for the merged
head in the check that it is on `origin/main`. The Controller never
deletes a branch, and `git branch -d` refuses the leftover
`milestone/<id>`, because its commits are not ancestors of `main`.
After close-out, delete it with `git branch -D milestone/<id>`.

**Do not press GitHub's "Update branch" button** while a milestone is in
flight. It pushes a merge commit to the milestone branch on the server,
which the next step refuses (the remote branch is no longer an ancestor
of the local tip), and the refusal names the button as the likely cause.
Use the `integration_required` procedure instead.

While a binding is active, workers additionally run with `gh`,
`git push`, `git rebase`, `git switch`, `git checkout -b` and
`git reset --hard` disallowed. That is defence in depth only; the
guarantee is that after every worker job `HEAD` must still be on the
bound branch and the new tip must descend from the old one, or the job
fails (`BranchInvariantViolated`).

## When a milestone gets stuck: the refusal-state exits

A binding can reach one of two refusal states, which block every step --
from the branch and from `main` -- until one exit runs. The exits never
touch a ref or a pull request:

- **`pr_closed_unmerged`**: the Draft PR was closed without being merged.
  The three exits are **exclusive** -- choose one:
  1. **reopen** the PR on GitHub; the next step on the branch notices and
     continues. GitHub cannot reopen a PR whose head branch was deleted
     on GitHub: restore the branch there first, or take one of the other
     exits;
  2. `workflow-controller --work-item <id> milestone-binding --new-pr <repo>`:
     continue on the same branch with a new Draft PR, opened with the
     next branch commit. The old PR is recorded as superseded and is
     never adopted again; do **not** reopen it afterwards (a reopened
     superseded PR is refused by name until you close it again). If the
     branch was already merged into `main` by hand, without a PR, this is
     the exit to take: the next step finds nothing to open and records
     the milestone `CLOSED`;
  3. `workflow-controller --work-item <id> milestone-binding --abandon <repo>`:
     retire the binding. Admitted only while `origin/main`'s committed
     `WORKFLOW_STATE.json` has no unfinished entry for the work item:
     neither Workflow 2.5.1 nor 2.6.0 can retire a work item whose state
     already reached trunk.
- **`merged_before_acceptance`**: the PR was merged before the
  `/accept-milestone` commit. Later branch commits would never reach
  `main`. The exit is `--new-pr`, which continues the work item on the
  same branch (its next PR will meet `integration_required`, because
  `main` holds the merge commit). `--abandon` is named only when its
  precondition holds. In squash mode the continuation's PR usually also
  conflicts with `main` in `WORKFLOW_STATE.json` (the first squash and
  the continuation's acceptance both change the work item's entry), and
  GitHub then disables "Squash and merge". Resolve the conflict on GitHub
  (which merges `main` into the branch there), then squash-merge.
  Close-out then refuses once, naming `git merge --ff-only <resolved head>`
  for the local branch; after that it closes out. Without a conflict, the
  squash is verified on the `integration_required` path. If `origin/main`
  already records the item `MILESTONE_COMPLETE` without an acceptance
  commit (a phase set by hand), `--new-pr` would only lead back here, so
  the gate names `--abandon` alone and `milestone-binding --new-pr`
  refuses.

Two more situations have their own exits:

- **`bound_item_missing`**: the plan was discarded (or stashed) after the
  bind, so the bound work item is gone from the branch's working tree.
  The Controller will not plan a second milestone on the bound branch.
  Restore the plan files (`git restore`, or `git stash pop`), or, when
  nothing was committed or pushed on the branch yet, run `--abandon` and
  then delete the leftover local `milestone/<id>` before planning the
  same id again.
- **A bind interrupted by a crash** leaves a `BRANCH_PLANNED` record. The
  next step completes it when it can; otherwise the refusal names the
  exit for what it observes: switch to a `milestone/<id>` the bind
  created (at the recorded branch point or a descendant); remove or
  rename one it did not create; when none exists, create
  `milestone/<id>` at the current `main` tip (or at the recorded branch
  point if `main` no longer descends from it) and switch to it; or
  `--abandon` if the plan was discarded. Creating the branch after
  `main` was rewound below the work item's `base_commit` opens a Draft
  PR in the plan-stage window that carries the commits the rewind
  removed from `main`.

The Controller never deletes a branch. To plan an abandoned id again,
first delete its old branches yourself: `git branch -d milestone/<id>`
(`-D` only if you mean to discard commits on it, or after a squash merge,
which `-d` does not recognise as merged) and
`git push origin --delete milestone/<id>`. The bind then renames the old
record to `<work_item_id>.abandoned-<n>.json`, and that binding's PR
numbers stay excluded from every later binding of the id.
