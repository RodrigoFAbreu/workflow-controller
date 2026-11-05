# H4e: Codex worker, step 5

Item id: `workflow-controller-h4e`.

## Roadmap row (moved verbatim)

Complexity: medium. Codex worker, step 5: the four review-side roles (`review-plan`, `review-implementation`, `apply-plan-review`, `apply-implementation-review`) can be routed to Codex; a safety review and a live proof on a scratch repository (operator-run)

Needs: H4c2, H4d, H1, the Codex worker works in a separate clone of the managed repository and the Controller lands its commits fast-forward (owner decision, 2026-11-02)

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)

## Split (2026-11-05)

H4e is split at the first review by the sizing rule ([#303](https://github.com/RodrigoFAbreu/workflow-controller/issues/303)). This item is the clone and the fast-forward landing. H4e2 routes the four review-side roles, with the safety review and the live proof. The row above is kept as it was written; the four-roles part of it now belongs to H4e2.

## What exists
`job.CODEX_WORKTREE_PREPARER` (`controller/job.py`, `(managed_repo, layout) -> Path`, `None` by default: the launch is then refused with `WorkerNotStarted`), the worker sandbox's layout (`codex_worker_sandbox.WorkerLayout(runtime_root, job_id)`), the job record and usage/ownership/reaping (H4c1, H4c2, H4d), and the documented consequence of a clone that is not the managed repository: a finished Codex job does not prove its effects landed (`phase_not_in_to_any_of` on a legacy target, `no_progress` on a protocol decision). Read `docs/guide/harnesses.md` ("A Codex worker run as a job record" and the "deliberately undecided" paragraph, which this item closes).

## Scope, one mechanism (a `chore:`; the seam stays dark until H4e2, so no behaviour change and no release)
1. **The preparer.** Clone the managed repository into the job's layout under the runtime root (a local `git clone --no-hardlinks`, no hooks, `core.hooksPath=/dev/null`, `gc.auto=0`; the clone's own `.git`, never a `.git` file pointing into the managed repository), check out the work item's branch at the commit the Controller recorded, and record that base commit in the job record. The worker never gets a way to write into the managed repository (no remote it can push to).
2. **The landing.** When the job ends, the Controller (never the worker) fetches the clone's branch and fast-forwards the managed branch, **only if**: the managed branch is still at the recorded base; the clone's commits are linear descendants of it (no merge commit); and nothing outside the branch changed (no other refs, no hook or config changes). Otherwise the job ends `FAILED` with a named reason and the clone is kept for inspection. A landing is a git write in the managed repository, so it runs only where the Controller already writes (under its lifecycle lock, between jobs, never while a review job reads the repository: a change of refs or config during a Codex review ends it `ReviewRepositoryChanged`).
3. **Reconcile sees it.** After a landing the managed repository is as if the worker had worked in place, so the protocol decision's `reconcile` finds the progress (or the legacy phase check passes); a job that landed nothing keeps today's `no_progress`.
4. **Cleanup.** The clone is removed through `runtime.remove_tree` at job end (C10k refuses a path below a link) except a kept-for-inspection clone, which retention removes later; it must not leak into `/tmp` (the runtime root is not `/tmp`; the Controller's own test leftovers there already cost memory).

## Decisions the plan must settle
- Where the clone lives and how the layout names it; the exact fast-forward mechanism (`git fetch` from the clone path into a private ref, then `merge --ff-only`) and what the working tree of the checkout does.
- What a "job's own commits" check verifies (count, linear, the author and trailer shape the Workflow requires), and what the Workflow's protected-path rules mean for a worker that changes a protected file: the landing must not weaken any approval check; ask rather than guess.
- Safety review as part of the plan: the clone cannot reach the operator's credentials beyond what H4b already allows; the landing cannot be tricked by a symlink, a submodule, a hook or a crafted ref.
- A test for every refusal of the landing, and a scratch-repository live proof that is operator-run (like the other Codex proofs).

## Out of scope
Routing any real role to Codex (H4e2), `milestone-implement` (H4f), the settings or options for it, the Manager's or Workflow's use. Complexity: medium. If the plan exceeds 5 checkpoints, split as CP1 the preparer and its record, CP2 the landing and its refusals, CP3 reconcile, cleanup and the pages.

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
