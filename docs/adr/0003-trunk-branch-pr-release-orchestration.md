# ADR 0003: Trunk branch, pull request and release orchestration

Status: accepted. See
`docs/ai-workflow/CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md` for the
full design record (work item
`workflow-controller-trunk-branch-pr-release-orchestration`). This
document records the decisions and the alternatives they rejected. It
adds no exit code: the table in
[ADR 0001](0001-controller-generation-1-architecture.md) stays the
normative exit-code contract, unchanged. The new `milestone-binding`
subcommand reuses exit codes `0` and `20`.

## Context

Through 1.1.1 every milestone ran directly on `main`, and a release was a
hand-pushed tag that triggered `release.yml`. That process produced
`v1.1.0`, a tag with no release, which a later bump silently stepped
over. The version was declared in `controller/version.py` and read back
by `pyproject.toml`, so two files could disagree about it. The Controller
had no Git or GitHub footprint of its own beyond reading the target's
history.

The goal was a lightweight, reusable trunk-based model: one short-lived
branch and one Draft pull request per milestone, a human as the only
merger, and a release published from `main` whenever the version
changes, with this repository as the first adopter. The two Generation 1
invariants hold unchanged: the Controller never writes Workflow lifecycle
state, and it never crosses a human gate.

## Decisions

### The Workflow / Controller boundary

- **Workflow owns review identity; the Controller owns refs and forge
  objects.** Workflow owns lifecycle state, `review_content_id`,
  approvals, provenance intervals and `base_commit`. The Controller owns
  the Git refs it creates, the pull request and release objects, the
  repository policy and its own binding records. The Controller never
  grows a competing base or review model.
- **Drift is detected, never integrated, under Workflow 2.5.1.**
  Workflow 2.5.1 has no transition that re-establishes review against a
  moved base, and any Controller attempt would either pollute review
  diffs or break provenance. So readiness fails closed with the
  `integration_required` gate, and the supported path is a human merge
  with "Create a merge commit". `gitrepo.merge_trunk` (a
  history-preserving `git merge --no-ff`) is implemented and tested in
  disposable repositories only, and no lifecycle path calls it.
- **Integration with Workflow 2.6.x is a separate milestone.** Earlier
  plan revisions kept a CP11 that waited for a released Workflow 2.6.x.
  It was split out (the user's decision): this milestone finishes under
  Workflow 2.5.1, and a follow-up milestone, started only after both this
  milestone and the Workflow Manager 2.6 milestone are complete, measures
  the released contract and answers the open questions E1-E5 (recorded
  branch/base, a base-moving transition, provenance across an
  integration merge, the phases where integration is legal, and merging
  `WORKFLOW_STATE.json` across branches). Waiting on an undated release
  would have blocked this milestone's acceptance, the 1.2.0 release and
  dogfooding.

### Activation and repository policy

- **Explicit, committed activation.** Everything here is off unless the
  target commits `.workflow-controller/policy.json`. Without it (and
  without a binding record) decisions, job records, worker argv, CLI
  output and exit codes are byte-identical to 1.1.1; the only cost is a
  fixed probe of two read-only `git` calls (four on an unborn `HEAD`).
- **Read from a committed tree, never the working tree.** A working-tree
  existence check was rejected: a committed policy deleted in the
  worktree would silently deactivate. A binding records a snapshot of
  the policy at its branch point and is governed by it; a policy edited
  on the milestone branch is never the one adopted.
- **Fail closed on an inadmissible policy.** Unknown schema versions,
  keys, adapter kinds or invalid values refuse every lifecycle command.
- **Generic capability, reference configuration.** `repo_policy.py`,
  `gitrepo.py`, `forge.py`, `milestone_branch.py` and `release_txn.py`
  name no trunk, tag prefix, version file, artifact type or repository.
  Version sources, schemes, tag formats, build/verify commands, artifacts
  and publication are closed adapter registries. This repository's
  wheel-specific checks stay in `tools/release.py verify-wheel`, reached
  only through the policy's `verify` command.

### Version authority

- **`pyproject.toml`'s static `[project].version` is the only
  human-maintained version.** A package runtime reads its own
  distribution's metadata, cross-checked against `BUILD_INFO.json`; a
  source runtime reads its own code root's `pyproject.toml`, never
  `importlib.metadata`, which can describe a different installed copy.
  The dynamic-version indirection through `controller/version.py` was
  removed; that module is now a stdlib-only resolver.

### Milestone branch binding

- **Bind after `/milestone-plan`, in the uncommitted plan-stage
  window.** Workflow 2.5.1 derives the work-item id itself, so the
  branch cannot be named before planning. An operator-supplied id with
  the branch created first would need `/milestone-plan` to accept an
  unknown id, a Workflow change that was recorded (E1) rather than worked
  around. The bind carries the uncommitted plan to `milestone/<id>`, so
  the plan approval commit lands on the branch; an approval already on
  trunk refuses with manual-recovery guidance.
- **Durable binding records under the runtime root, keyed by the Git
  common directory.** Records live in
  `<runtime_root>/repositories/<repo_key>/milestones/`, never in Workflow
  state, with an append-only event log. Every Git or GitHub mutation is
  preceded by an intent record and followed by a verified outcome record
  (persist before act), and restart reconciles from the live refs and
  pull requests. The transition relation is a closed table; any other
  write raises and writes nothing.
- **Identity is verified, not assumed.** A pull request is the
  milestone's only if number, head, base, repository and the
  non-cross-repository flag all match. Zero matches create, exactly one
  open match is adopted, anything else refuses. Undecidable `git`/`gh`
  reads refuse; they are never treated as "absent".
- **The Controller changes `HEAD` in exactly three places**: the bind,
  completing a crash-interrupted bind at the same commit, and close-out
  after a verified merge. Anything else that finds `HEAD` elsewhere
  refuses.
- **Refusal states have operator exits, and abandoning is narrow.** A PR
  closed unmerged or merged before acceptance blocks until the PR is
  reopened, `milestone-binding --new-pr` continues on the same branch, or
  `milestone-binding --abandon` retires the binding. `--abandon` is
  refused while the work item's non-terminal state is on trunk, because
  Workflow 2.5.1 has no transition that retires a work item. Deleting
  the record file by hand was rejected: it would leave the old branch
  adoptable and leave no audit event. Abandoned records are renamed
  aside, never deleted, and their PR numbers stay excluded from later
  bindings of the same id.
- **A milestone merged without a PR is closed out, not abandoned.** A
  new refusal state exited through `--abandon` was rejected because it
  would record a completed milestone as abandoned.

### Pull request lifecycle

- **Draft until accepted; ready means accepted.** The PR is marked ready
  only when its head is exactly the acceptance commit (the first commit
  whose committed state records the work item `MILESTONE_COMPLETE`,
  carrying `/accept-milestone`'s `Workflow-Work-Item` trailer), the fresh
  remote trunk is an ancestor of it, and, by policy, checks are green.
  Commits after acceptance gate rather than being removed.
- **No merge operation exists.** The forge boundary has no merge call and
  a static test forbids the spellings. A human merges every PR.
- **Only history-preserving merges converge cleanly.** The policy has no
  merge-method field, because only one value is admissible. The runbook
  disables squash and rebase merging in repository settings, and
  close-out is the fail-closed backstop (`MERGED_REWRITTEN`).
- **Automatic close-out** switches to trunk and fast-forwards after a
  verified merge, with a clean tree only.
- **Worker tool restrictions apply only with an active binding.**
  Applying them everywhere would change 1.1.1 behaviour for unconfigured
  targets. They are defence in depth; the guarantee is post-step
  verification that `HEAD` is still on the bound branch and the tip only
  moved forward.
- **Network failure during a preflight refuses**, even for plan-stage
  steps: availability is traded for never acting on a stale view of the
  remote.

### Release transaction

- **Desired state, not diff.** A trunk commit is classified against the
  last release tags in its own history, not against
  `github.event.before`, so a failed or cancelled run is retried by any
  later run carrying the same version, and skipping a pending run loses
  nothing. The diff-based alternative can permanently skip a version.
- **Tag after validation, never moved.** The tag is created only after
  the full validation matrix and artifact verification for that exact
  commit, then pushed as a new ref. Publication failures after the tag
  push are resumed at the tag's own commit (`RESUME`), by the next trunk
  push or `workflow_dispatch`, never by moving the tag.
- **No tag is ever skipped over.** Every lower ancestor tag without a
  published release blocks a higher version (`BASELINE_UNRELEASED`) until
  it is resumed or explicitly acknowledged in `abandoned_tags`, and
  acknowledging settles only that tag. Inferring "abandoned" from "tag
  without release" was rejected; it is how `v1.1.0` was lost. `v1.1.0` is
  acknowledged in the reference policy.
- **Asset consistency instead of byte comparison.** A rebuilt wheel is
  not byte-reproducible, so a published release is checked by asset
  names, `SHA256SUMS` and the policy's `verify` command for the target
  commit. A draft is completed from its present assets, which must
  verify, and is never deleted.
- **No version bump inside the milestone.** 1.2.0, the first automatic
  release, is a post-acceptance release-preparation commit.

### CI/CD

- `main.yml` (push to `main` and `workflow_dispatch`, no inputs) runs
  validate, release-plan, build and publish as one transaction, never
  cancelled; only `publish` can write, and every action outside
  `validate` is SHA-pinned. `ci.yml` validates pull requests with
  same-ref cancellation. `release.yml` is removed, so a hand-pushed tag
  triggers nothing. A `workflow_dispatch` input naming a commit or tag
  was rejected: `RESUME` already targets the tag's commit, and a second,
  user-typed target source would be a new way to release the wrong
  commit.
- `validate.yml` keeps its jobs and ADR 0002's major-tag actions, and
  gains a `trunk` shard for the new modules.

## Consequences

- A repository opts in with one committed file; others are unaffected.
- Every milestone of an opted-in repository is reviewed as one Draft PR
  and merged by a human; the Controller's own records say which branch
  and PR belong to which milestone, and survive crashes at every write.
- Under Workflow 2.5.1, anything that lands on `main` during a milestone
  (including this repository's own release commits) ends that milestone
  at `integration_required`, followed by the manual merge. This is the
  accepted contract until the follow-up integration milestone.
- Losing the runtime root mid-milestone is recoverable by re-adoption in
  the common case; a remote-only branch, or two merged PRs for one
  branch, needs the runtime root restored from a backup.
- Releases are published only from validated trunk commits, tags are
  immutable, and an interrupted release is always either resumed or a
  hard stop.
- Broader multi-worktree concurrency, other forges, and adoption tooling
  for other repositories remain future work (`docs/ROADMAP.md`).
