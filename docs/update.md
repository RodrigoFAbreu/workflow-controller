# Update, roll back and move a repository to a newer Workflow

> For: anyone who already runs the Controller. Last checked with: Controller 1.7.0; Workflow 2.6.0, 2.7.0 and 2.8.0.

Goal: change the Controller's version safely, or move a repository to another Workflow release.

## Prerequisites

- Nothing is running: `workflow-controller status` shows nothing active. Update between jobs, not during one.
- The new release's wheel and `SHA256SUMS`, downloaded and checked as in [install](install.md).

## Update the Controller

1. Check that nothing is running.

   ```bash
   workflow-controller status
   ```

2. Replace the old install with the new wheel. When pipx uses its uv backend (its default when uv is installed), `--force` fails to reuse an existing install, so uninstall first. Your settings file is not part of the install and stays.

   ```bash
   VERSION=<new version>
   pipx uninstall workflow-controller
   pipx install ./workflow_controller-$VERSION-py3-none-any.whl
   workflow-controller --version
   ```

What an update does to a Controller that is already running:

- A running `step` or `run` executes from its own snapshot of the installed package, so the new files never reach it.
- A new release of the same [generation](glossary.md#generation) is ignored by a running `run`, which finishes on the old version.
- A new generation stops a running `run` at its next boundary with exit 50 (all statuses are in [exit codes](exit-codes.md)) and a handoff record. Run again with the new version.
- If the reinstall lands in the middle of a boundary check, or the update changes Python's minor version, the run fails closed with exit 20. Run `run` again.

## Roll back

Install an older wheel the same way: `pipx uninstall workflow-controller`, then `pipx install <older wheel>`. Going back across a generation has two consequences:

- A [job](glossary.md#job) record written by the newer generation is refused by the older one, and `resume --abandon` refuses it too. Run `workflow-controller resume .` before you roll back and the problem does not arise. If you already rolled back and hit it (`StaleJobRecordError`), reinstall the newer version, let its own `resume` clear the record, then roll back.
- A `run` still executing the newer generation stops at its next boundary with exit 20.

Rolling back to 1.2.1 refuses a repository already on Workflow 2.6.0 (exit 20). In the same way, rolling back below 1.7.0 refuses a repository already on Workflow 2.7.0 or later (`outside_supported_line`, exit 20; see [compatibility](compatibility.md)).

## Move a repository to a newer Workflow

A repository moves to a new Workflow release through Workflow Manager, never by hand, and the Controller must admit the new release first: install a Controller that admits it before the move. [Compatibility](compatibility.md) has the table; Workflow 2.7.0 or later needs Controller 1.7.0 or later. The Manager's `update` replaces the managed files and rewrites `installation.json`. It commits nothing and migrates nothing.

Workflow 2.8.0 makes the approval gates automatic unless the repository has a gate policy, so decide the policy before the move (see [run](run.md#steps), step 3).

Do it between milestones, with no work item in flight and nothing running, on a branch of its own.

```bash
git switch -c chore/workflow-update main
workflow-manager --release-version <release> update .
workflow-manager verify .
workflow-controller inspect .
git status
```

`inspect` must print the new release without a refusal, and `git status` must show only the files the Manager wrote. Commit exactly those, open a pull request and merge it. Under the `conventional_commit` release trigger, give it a title that releases nothing, such as `chore: move to a newer Workflow`.

Never move a repository while a worker runs or while a plan-approval transaction is unfinished. A job that straddles the move ends in `workflow_release_changed` and the next invocation decides again. The quick fix is in [common problems](common-problems.md#a-step-refuses-with-workflow_release_changed); the long account is in [Workflow release changes](guide/troubleshooting.md#workflow_release_changed-and-workflow_release_changed).

### In the middle of a milestone

Updating a milestone's own branch in place is proven only at these phases:

- `AWAITING_LOCAL_PLAN_REVIEW`, `REVISING_PLAN` and `AWAITING_PLAN_APPROVAL`, including a bundle a 2.5.1 withdrawal left behind (the Controller stops at a gate whose steps regenerate it);
- `IMPLEMENTING`, between checkpoints.

At any other phase an in-flight update is untested. After a 2.5.1 to 2.6.0 move, `/milestone-plan` at `IMPLEMENTING` no longer re-plans: 2.6.0 routes a plan change to `/request-plan-amendment`, which only a human runs.

If you update the trunk while a milestone branch stays on the old release, the branch keeps its release and readiness still ends at `integration_required`. After the manual merge the close-out completes, and the same step then refuses once with `WORKFLOW_RELEASE_CHANGED` (the close-out is recorded); the next invocation admits the trunk's release. Workflow 2.6.0's lifecycle lock reads the `installation.json` committed at `HEAD` of every registered worktree, so a linked worktree whose branch predates the update counts as lagging. The Controller itself creates no linked worktrees.

## What you should see

After an update, `workflow-controller --version` prints the new version. After a Workflow move, `workflow-manager verify .` succeeds and `workflow-controller inspect .` prints the new release (`repository: ... (Workflow <release>, ...)`) and no refusal. On Workflow 2.8.0 `inspect` also prints an `advisory:` line about action ids this Controller release does not know; that is expected (see [compatibility](compatibility.md)).

## If it fails

- `inspect` refuses the repository as unsupported: the Controller does not admit that release. See [common problems](common-problems.md#the-repository-is-refused-as-unmanaged-or-unsupported-exit-20) and [compatibility](compatibility.md).
- A step refuses with `WORKFLOW_RELEASE_CHANGED`: the repository's release changed under a running step. Nothing was launched; run again.

Related: [install](install.md), [run](run.md), [release history](release-history.md).
