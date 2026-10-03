# Milestone branches and pull requests

> For: operators of a repository that uses milestone branches, pull requests and releases. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

[Back to the documentation map](../README.md)

The branch and pull request each milestone gets, the readiness gates, merging and close-out, auto-merge, and getting out of a stuck milestone. The sections below start with how one milestone runs.

## How a milestone runs under the policy

This behaviour is **off** unless the target commits
`.workflow-controller/policy.json` with `milestone_branches.enabled`. The
policy is read from the committed tree at `HEAD`, never from the working
tree, and an inadmissible policy (an unknown schema, key or adapter kind,
or an invalid value) refuses every lifecycle command rather than being
ignored. This repository's own policy is the reference configuration:
trunk `main` on `origin`, forge `github`, branches
`milestone/{work_item_id}`, Draft pull requests that become ready only
with green checks, merged with "Squash and merge" (see the
[cutover](ci-and-releases.md#cutover-from-the-version-file-model) for how a
repository moves to squash merging).

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

With auto-merge on (squash mode only, [below](#auto-merge-and-the-release-wait)),
the last line reads instead:

```text
     -- readiness: PR marked ready -- the Controller squash-merges the acceptance commit
     -- release wait: the release of the squash commit is published -- close-out -- stop
```

In merge mode the flow is the same, but the PR is titled with the
work-item id, readiness edits nothing, the human merges with "Create a
merge commit", and every text below that names "Squash and merge" names
"Create a merge commit" instead.

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
  | `release_notes_invalid` | squash mode with release notes: the milestone's notes section cannot be carried by the squash commit (see [below](#release-notes-in-the-pull-request-body)); the PR is not edited | mark the PR ready and merge it on GitHub ("Squash and merge"), then supply the notes at release |
  | `merge_pull_request` | the PR is ready | merge it on GitHub with "Squash and merge", without editing the commit message |
  | `merge_pending` | auto-merge: the Controller has not merged the ready PR yet (GitHub has not computed it mergeable, something other than the checks blocks it, a merge was refused, or GitHub accepted the merge and does not show it yet), or it cannot (`DIRTY`: a conflict) | wait (`run` waits for you, except on a conflict); or merge it on GitHub with "Squash and merge" |
  | `merge_held` | auto-merge: the ready PR was converted back to a draft | mark it ready for review (the next step merges it), or merge it on GitHub with "Squash and merge" |
  | `release_pending` | auto-merge: the squash commit's release is not published yet | wait (`run` waits for you); if no run of the publishing workflow appears, publish by hand |
  | `release_failed` | auto-merge: the release did not publish (a publishing run failed, or one succeeded without publishing) | "Re-run failed jobs" on the named run if there is one, or publish by hand; the next step classifies again |

  The four auto-merge gates are described in
  [Auto-merge and the release wait](#auto-merge-and-the-release-wait).

- **Merge and close-out.** You merge, unless the repository opted in to
  [auto-merge](#auto-merge-and-the-release-wait); then the Controller
  merges the acceptance commit itself. Either way, the
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
  and last lines below, and readiness adds the middle one. No paragraph
  in it parses as a Git trailer block. With release notes configured,
  readiness also puts the milestone's notes block above these lines (see
  [below](#release-notes-in-the-pull-request-body)).

  ```text
  Milestone `<id>`, planned in `<plan path>`, driven by workflow-controller.
  Accepted at <acceptance commit> on `milestone/<id>`; merge with "Squash and merge".

  <!-- workflow-controller: work_item=<id> -->
  ```

  When the binding's policy opts in to auto-merge (see
  [below](#auto-merge-and-the-release-wait)), the middle line ends
  `squash-merged into the trunk as one commit.` instead, whoever merges.
  The wording follows the policy the binding was bound with, not the
  `merge.auto` setting, so turning the setting off does not edit the
  pull request.

- An edit at readiness ends the step at `checks_pending`: the title
  check re-runs, and the PR is marked ready only on a later step that
  finds nothing to edit.

**Merge with "Squash and merge", and do not edit the commit message**
in GitHub's merge dialog. The repository's default squash message
("Pull request title and description") is what close-out recognises,
and it is how the notes block reaches the release.

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

## Release notes in the pull request body

In squash mode a milestone's release notes can travel in its pull
request body, through the squash commit, to the release. A repository
opts in with the optional policy key
`milestone_branches.pull_request.release_notes`, which says where a
milestone's notes live:

```json
"release_notes": {"path": "docs/milestones/completed/{work_item_id}.md", "heading": "Release notes"}
```

`path` is a relative, normalised POSIX path, and its only placeholder is
`{work_item_id}`. `heading` is a non-empty single line. Without the key
the body is the one [above](#squash-merges-and-the-pull-request-title),
unchanged. Controller 1.4.x refuses a policy that has the key. The
release side, the `{release_notes}` placeholder, is in
[Release notes from the milestones](ci-and-releases.md#release-notes-from-the-milestones).

**Where readiness reads the notes.** Like the merge mode, the path and
heading come from the policy snapshot taken when the branch was bound,
so a milestone keeps the notes location it started with. A policy edit
on the milestone's own branch does not move it, and a milestone bound
before the repository opted in carries no notes. Readiness reads the
file at the acceptance commit (never the working tree) and takes the
section under the line `## <heading>`, up to the next `## ` line or the
end of the file, with outer blank lines trimmed. The body becomes:

```text
<!-- workflow-controller: release-notes work_item=<id> sha256=<digest> -->
<notes>
<!-- workflow-controller: release-notes end -->

Milestone `<id>`, planned in `<plan path>`, driven by workflow-controller.
Accepted at <acceptance commit> on `milestone/<id>`; merge with "Squash and merge".

<!-- workflow-controller: work_item=<id> -->
```

(with the auto-merge wording of the middle line when the policy opts in,
as above)

`<digest>` is the SHA-256 of the notes' UTF-8 bytes. Only readiness
writes this start marker, and only for notes that passed the checks
below; the release checks the digest. When the file or the section is
missing, or the section is empty, the body carries no block and
readiness does not refuse. A release that uses `{release_notes}` then
refuses unless the notes are supplied later
([the fix](ci-and-releases.md#release-notes-from-the-milestones)), so
write the section. The `pr_edited` and `ready` events record
`release_notes: absent`, `empty` or `included` when the snapshot
configures notes.

**Write the section for the squash commit.** GitHub rewraps lines of
the squash commit body longer than 72 characters, so the notes must
reach it with no line that long. Readiness checks the notes, and the
whole body around them, before any edit:

- wrap the section at 72 bytes per line: 72 columns of ASCII, fewer
  characters on a line with accented letters, which take two bytes
  each;
- no line ends in a space;
- no tab and no carriage return (use LF line endings);
- no Controller marker text (`<!-- workflow-controller:`);
- no paragraph (a run of non-blank lines), read on its own, parses as a
  Git trailer block;
- the whole body holds at most 65536 characters, GitHub's limit.

The trailer rule refuses two ordinary shapes: a one-line paragraph such
as `Note: this release changes the default.`, and a paragraph that is
only a bare URL (`https://example.com/x` parses as a trailer). Reword
the paragraph or join it to its neighbour. A colon inside prose or a
list item passes, and so do `Upgrade note: ...` (a space in the key),
`**Breaking:** none` and a Markdown table. The check runs
`git interpret-trailers --parse` with no Git configuration at all, so a
local `trailer.separators` setting does not change the answer.

A work-item id longer than 62 characters makes the marker's
`work_item=` token longer than 72 characters. That one token cannot be
broken at a space, and how GitHub wraps it has not been measured.

**`release_notes_invalid`.** Notes that break a rule, a body that is
too long, or a notes file that is not valid UTF-8 gate
`release_notes_invalid`. The gate names the path, the line number or
the paragraph's first line, and the rule. The pull request is not
edited, and it is not marked ready. This cannot be fixed on the branch:
a commit that fixes the section follows the acceptance commit, so
readiness would stop at `post_acceptance_commits`, and the plan can no
longer be amended. The exit is to mark the pull request ready and merge
it on GitHub with "Squash and merge", without notes, and then to supply
the notes at release with `python3 tools/release.py notes-block
--work-item <id> <file>` (see
[the fix](ci-and-releases.md#release-notes-from-the-milestones)). To
avoid it, wrap the section at 72 columns while you write it.

## Auto-merge and the release wait

A repository can let the Controller merge an accepted
milestone, wait for its release and close it out, so that nothing is
left to a person after `/accept-milestone`. It opts in with two optional
policy keys:

```json
"pull_request": {"draft": true, "ready_requires_green_checks": true, "merge_method": "squash",
                 "auto_merge": true, "release_workflow": "main.yml"}
```

- `auto_merge` is a boolean, default `false`. `true` requires
  `merge_method: "squash"` and `ready_requires_green_checks: true`;
  otherwise the policy is refused, naming the field. `true` with
  `release.enabled: false` is admitted: the milestone then closes out
  without a release.
- `release_workflow` names the workflow file under `.github/workflows/`
  whose run on a push to the trunk publishes the release. It defaults to
  `main.yml`, and must be a file name without a `/`. Only the release
  wait reads it.

Controller 1.5.x refuses a policy that has either key. Like the merge
mode and the release notes location, both come from the policy snapshot
taken when the branch was bound, so a milestone bound before the
repository opted in never auto-merges, and a policy edit never changes a
milestone in flight. GitHub's own "Allow auto-merge" setting is not used.

**The operator's switch.** The settings file's `merge.auto` (default
`true`, see [the runtime guide](runtime.md#what-it-holds)) turns the
merge off for every repository on the machine: readiness then ends at
`merge_pull_request` as without the key (its text names the setting:
"Auto-merge is switched off by `merge.auto` in the settings file"), and a
person merges. The
release wait and the stop after close-out still follow the binding's
policy, because they only read. `merge.auto` is read at every step, so
turning it off mid-milestone takes effect at the next one; nothing is
pending on GitHub to withdraw.

**The merge.** For a `READY` record, each step re-reads the pull request
and its checks, and the first of these that holds decides:

1. the pull request is merged: close-out continues as for any merge;
   closed unmerged: `pr_closed_unmerged`, as always;
2. the remote branch is not an ancestor of the local tip (someone
   pushed on GitHub, or pressed "Update branch"): the usual "Update
   branch" refusal;
3. the local tip is not the acceptance commit (a commit after it, pushed
   or not): `post_acceptance_commits`. The Controller merges only the
   accepted head; merge anyway on GitHub if you want the later commits;
4. GitHub accepted an earlier merge that is not visible yet, an earlier
   send's squash commit is on the trunk, or the three attempts are spent
   and one's outcome is unknown (below): `merge_pending`, without sending
   again. A read that still shows the pull request open, with whatever
   checks and merge state, does not change this;
5. the pull request's head is not the acceptance commit (GitHub's read
   lags, or the remote branch is gone): `pr_head_not_accepted`;
6. the pull request is a draft again: `merge_held`. Converting it back to
   a draft is how a person holds the merge, and the Controller respects
   it;
7. a check failed, is pending, or was cancelled: `checks_failing`,
   `checks_pending` or `checks_cancelled`, exactly as at readiness. After
   a green "Re-run failed jobs" the next step merges;
8. GitHub reports a conflict (`DIRTY`): `merge_pending` at once, naming
   it; or that the branch must be up to date with the trunk (`BEHIND`):
   `integration_required` (the manual procedure above);
9. GitHub reports the pull request mergeable (`CLEAN` or `HAS_HOOKS`):
   the Controller sends the merge;
10. anything else (`UNKNOWN` just after `gh pr ready`, `BLOCKED` by a
    requirement other than the checks such as a required review,
    `UNSTABLE`): `merge_pending`, naming GitHub's state.

The merge is one call, `gh pr merge <n> --squash --match-head-commit
<acceptance commit> --subject "<title> (#<n>)" --body <body>`: GitHub
merges exactly the acceptance commit, or refuses. The subject and body
are the ones `MERGED_SQUASHED` verifies, so the repository's default
squash message does not matter. The Controller never sends `--auto`:
GitHub's auto-merge request would stay enabled after a later push by
someone with write access and merge a head nobody accepted. A request
you enable yourself is yours; the Controller neither reads nor withdraws
it, and treats the merge it makes like any hand merge.

Before each send the binding records the intent (`merge.state:
"sending"`, the head and the attempt count), so a crash anywhere is
safe: the next step re-reads and decides again. A failed send (a
refusal, or a lost reply, which `gh` reports the same way) and a send
interrupted before its outcome was recorded may still have merged, and
GitHub's pull-request reads can lag its write. So before counting a
failure as a refusal, and before sending again, the Controller fetches
`main` and looks for the pull request's squash commit (a first-parent
commit since the acceptance commit's merge base whose subject ends in
`(#<n>)` and whose tree is the acceptance commit squashed onto its
parent, so an unrelated commit that only carries the suffix is not
taken for it). When it is there, the merge is recorded as accepted
(`merge.squash_commit`), nothing more is sent, and the step waits for
GitHub to show it. Nothing depends on the wording of GitHub's refusal.
What `gh` itself reports, observed against GitHub (these are the
fake `gh`'s texts too; nothing depends on any of them):

| send | exit | output |
| --- | --- | --- |
| a draft pull request | `1` | `GraphQL: Pull Request is still a draft (mergePullRequest)` |
| a head other than `--match-head-commit` | `1` | `GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)` |
| the right head, ready | `0` | no output; the base moves |
| a pull request that is already merged | `0` | `! Pull request <owner>/<name>#<n> was already merged`; `gh` reads the state first and sends nothing |

So a send that finds the pull request merged (a reply lost earlier, a
hand merge, a lagging read) is a success, not a refusal: it is recorded
as accepted, no refusal is counted, and the step waits for GitHub to
show the merge and closes out as for any merge. (A repository that
deletes merged branches removes the pull request's head branch itself.)
Otherwise a refused merge is `merge_pending` with GitHub's message, sent
again only on a later re-read that shows the pull request mergeable.
After three refused attempts the Controller refuses (exit `20`) and
sends nothing more for this pull request: merge on GitHub with "Squash
and merge" (the message names the cause, for example squash merging
turned off), and the next step closes out. When the three attempts are
spent but one of them was interrupted before its outcome was recorded,
the step waits instead (`merge_pending`, "may already have merged") and
sends nothing more. `milestone-binding --new-pr` (after you close the pull
request) starts a new merge record: the replacement pull request gets
its own three attempts, and an `accepted` merge of the closed one does
not carry over.

The merge happens only from the milestone branch. With `HEAD` on `main`,
a `READY` record's open pull request is refused with "switch to it", so
an unattended `run` started from `main` merges nothing.

**The release wait.** After a verified squash (`MERGED_SQUASHED`), before
close-out switches to `main`, the Controller classifies the squash commit
with the same release transaction `main.yml` runs, using the policy
committed at that commit, and reads only (it never downloads a release
asset, runs a policy command, tags, publishes or re-runs anything):

- releases off at that commit: settled (`NONE`);
- `ALREADY_RELEASED`: settled, recording the tag, version and URL
  (event `released`); `NO_CHANGE` or `ABANDONED_VERSION`: settled
  (event `release_settled`);
- a release due: the runs of `release_workflow` for that commit on the
  trunk decide. None reported yet, or one still running:
  `release_pending`. All completed without publishing: `release_failed`,
  naming each run that did not succeed, or saying the workflow
  succeeded and published nothing;
- a later push to the trunk can publish the release that covers the
  squash commit (`main.yml` cancels a pending run when a newer one
  queues, and any later run tags its own commit or resumes an
  unpublished tag). A published covering release settles the wait
  (`SUPERSEDED`, with that release's tag, URL and commit), even when no
  run for the squash commit itself was ever reported; a later trunk
  run still working keeps it `release_pending` rather than
  `release_failed`;
- any other classification: `release_failed` with its detail.

Runs of other workflows (such as `Workflow conformance`) are never read.
`release_failed` names the exits: "Re-run failed jobs" on the named run
(the release transaction resumes safely), or publish by hand with
`tools/release.py` (see
[Checking a release by hand](ci-and-releases.md#checking-a-release-by-hand)).
Every step classifies again first, so a release that appears later
settles the gate. While the squash commit's run is unfinished, each
poll also fetches the trunk and the tags to look for a covering
release. The binding records the settled `release` field.

**Close out, then stop.** Once the release is settled, close-out runs as
always (switch to `main`, fast-forward, `CLOSED`), and the step ends
there: exit `0`, no worker, no job record, and the run's `no_action`
event names the release (reason `closed_out_released`). The next `run`
or `step` starts from `main` and plans the next milestone with
`/milestone-plan <main tip>` as usual. (When the close-out ran from the
trunk side, with the checkout already on `main`, it does not fast-forward
`main`: the next step stops at `fast_forward_trunk` with `git merge
--ff-only <remote>/main`, then plans.) That pause leaves room for a
release-notes pull request before the next milestone takes `main`'s tip
as its base.

**A hand merge in an auto-merge binding.** A person can still merge
first. A verified squash goes through the release wait and the stop as
above. A merge commit ("Create a merge commit", if the repository allows
it) records `release` as `SKIPPED` (event `release_wait_skipped`) and
closes out exactly as without the key, launching `/milestone-plan`. A
rebase or an edited squash message is `MERGED_REWRITTEN` and never
closes out, as always.

**`run` waits, `step` does not.** The pending gates -- `checks_pending`,
`pr_head_not_accepted`, `merge_pending` (except a conflict) and
`release_pending` -- are waitable in an auto-merge binding: inside a
`run`, the step polls them every `merge.poll_seconds` (default 30) for
up to `merge.wait_seconds` (default 3600) in total, across gate changes,
launching no worker. So one unattended `run` goes from the readied pull
request to the closed-out, released milestone. The run log gets one
`waiting` event per gate (the gate and the deadline), and `follow` shows
`waiting at <gate> until <deadline>`. When the budget runs out, the step
ends at its last gate and writes its job record, as any gate does. Every
other gate ends the step at once. `step` never waits, and neither does
`merge.wait_seconds: 0`. Ctrl-C during the wait prints one line
(no traceback), exits `130` and ends the run
`interrupted` with no job record for the waiting step; the binding keeps
its last written state and the next step continues from it. A waiting
`run` holds the target's lifecycle lock, so a second `step` or `run`
meanwhile exits `45` (see
[Troubleshooting](troubleshooting.md#common-situations)).

**What you see.** `explain` predicts `merge` or `wait_merge` for a
`READY` record, and `wait_release` after the squash. `status` adds
`merge: <state> at <head> (attempt <n>)` and `release: <tag> <url>` (or
the settled state) to the binding's line when present, and `status
--json` and `inspect` add the binding's `merge` and `release` fields.
The binding's events add `merge_sent`, `merge_accepted`,
`merge_refused` (once per message), `released`, `release_settled`,
`release_wait_skipped` and `release_failed` (once per detail).

**Merge queues are unsupported.** In a repository that requires a merge
queue, `gh pr merge` adds the pull request to the queue, and the queue
writes its own commit message. The squash then fails verification and
the binding ends at `MERGED_REWRITTEN`, which never closes out.

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
