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
with green checks.

With the policy active, one milestone runs like this:

```text
main -- /milestone-plan -- bind milestone/<id> -- plan reviews -- plan approval (on the branch)
     -- push, Draft PR -- implementation, reviews, acceptance (each step pushed)
     -- readiness: PR marked ready -- HUMAN merges ("Create a merge commit")
     -- close-out: switch to main, fast-forward -- next /milestone-plan on main
```

- **Trunk start.** Before a bare `/milestone-plan`, `HEAD` must be on
  `main`, the tracked tree clean, and local `main` equal to
  `origin/main`. A `main` behind the remote gates `fast_forward_trunk`
  (the Controller does not fast-forward at start: you may have local
  work), and a diverged one refuses.
- **Bind.** After `/milestone-plan` has written the plan, and before
  anything about it is committed, the Controller creates
  `milestone/<id>` at `main`'s tip and switches to it, carrying the
  uncommitted plan along. Workflow 2.5.1 derives the work-item id itself,
  so the branch cannot be created earlier. A plan approval commit that
  already sits on `main` refuses with manual-recovery guidance.
- **Draft PR.** As soon as the branch has a commit beyond `origin/main`
  (normally the plan approval commit), the Controller pushes it and
  opens one Draft PR titled with the work-item id, whose body carries a
  `<!-- workflow-controller: work_item=<id> -->` marker line. Every later
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
  for that head with every check passing or skipped. Otherwise the step
  gates:

  | Gate | Meaning | What you do |
  |---|---|---|
  | `checks_pending` | no check reported yet, or one still running; the common case just after the final push | wait, then `run` again |
  | `checks_failing` | a check failed | re-run it on GitHub if it is flaky; a fix committed on the branch follows the acceptance commit, so readiness then gates `post_acceptance_commits` |
  | `checks_cancelled` | a check was cancelled, none pending or failing | re-run it on GitHub |
  | `post_acceptance_commits` | commits follow the acceptance commit | merge anyway on GitHub ("Create a merge commit"), or leave the gate standing; the Controller never removes them |
  | `integration_required` | `main` moved after the branch point | the manual merge procedure below |
  | `pr_head_not_accepted` | GitHub does not show the pushed acceptance commit as the PR head yet | `run` again once it does |
  | `merge_pull_request` | the PR is ready | merge it on GitHub with "Create a merge commit" |

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
  `git reset --keep <merged-head>` on `milestone/<id>`). A squash or
  rebase merge takes the reviewed commits off `main`: the Controller
  records it (`merge_method_rewrote_history`, shown once), does not
  switch, and blocks nothing afterwards. With `HEAD` still on a finished
  milestone's branch, every step gates `switch_to_trunk`.

**`integration_required` is the normal path, not an error.** Under
Workflow 2.5.1 there is no Workflow transition that re-establishes review
against a moved base, so the Controller never integrates `main` into a
milestone branch. Whenever anything lands on `main` during a milestone --
including this repository's own post-acceptance release commits -- the
milestone ends at `integration_required`, and the supported procedure is:
on GitHub, mark the PR ready and merge it with "Create a merge commit".
Close-out then converges as for any merge. This stays the contract until
the follow-up integration milestone binds integration to the released
Workflow 2.6.x (`docs/ROADMAP.md`). This is why this repository's ruleset
does not require a pull request to be up to date before merging (see
[Repository settings](ci-and-releases.md#repository-settings)).

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
     Workflow 2.5.1 cannot retire a work item whose state already reached
     trunk.
- **`merged_before_acceptance`**: the PR was merged before the
  `/accept-milestone` commit. Later branch commits would never reach
  `main`. The exit is `--new-pr`, which continues the work item on the
  same branch (its next PR will meet `integration_required`, because
  `main` holds the merge commit). `--abandon` is named only when its
  precondition holds. If `origin/main` already records the item
  `MILESTONE_COMPLETE` without an acceptance commit (a phase set by
  hand), `--new-pr` would only lead back here, so the gate names
  `--abandon` alone and `milestone-binding --new-pr` refuses.

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
(`-D` only if you mean to discard commits on it) and
`git push origin --delete milestone/<id>`. The bind then renames the old
record to `<work_item_id>.abandoned-<n>.json`, and that binding's PR
numbers stay excluded from every later binding of the id.
