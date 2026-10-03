# Update, roll back and move a repository to a newer Workflow

> For: anyone who already runs the Controller. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

Goal: change the Controller's version safely, or move a repository to another Workflow release.

## Prerequisites

- Nothing is running: `workflow-controller status` shows `active: none`. Update between jobs, not during one.
- The new release's wheel and `SHA256SUMS`, downloaded and checked as in [install](install.md).

## Update the Controller

1. Check that nothing is running.

   ```bash
   workflow-controller status
   ```

2. Install the new wheel over the old one.

   ```bash
   pipx install --force ./workflow_controller-$VERSION-py3-none-any.whl
   workflow-controller --version
   ```

What an update does to a Controller that is already running:

- A running `step` or `run` executes from its own snapshot of the installed package, so the new files never reach it.
- A new release of the same generation is ignored by a running `run`, which finishes on the old version.
- A new generation stops a running `run` at its next boundary with exit 50 and a handoff record. Run again with the new version.
- If `pipx install --force` lands in the middle of a boundary check, or the update changes Python's minor version, the run fails closed with exit 20. Run `run` again.

## Roll back

Install an older wheel the same way: `pipx install --force <older wheel>`. Going back across a generation has two consequences:

- A job record written by the newer generation is refused by the older one, and `resume --abandon` refuses it too. Run `workflow-controller resume .` before you roll back and the problem does not arise.
- A `run` still executing the newer generation stops at its next boundary with exit 20.

Rolling back to 1.2.1 refuses a repository already on Workflow 2.6.0 (exit 20).

## Move a repository to a newer Workflow

A repository moves to a new Workflow release through Workflow Manager, never by hand, and the Controller must admit the new release first: install a Controller that admits it before the move. [Compatibility](compatibility.md) has the table; Workflow 2.7.0 needs Controller 1.7.0 or later. The Manager's `update` replaces the managed files and rewrites `installation.json`. It commits nothing and migrates nothing.

Do it between milestones, with no work item in flight and nothing running, on a branch of its own.

```bash
git switch -c chore/workflow-update main
workflow-manager --release-version <release> update .
workflow-manager verify .
workflow-controller inspect .
git status
```

`inspect` must admit the new release, and `git status` must show only the files the Manager wrote. Commit exactly those, open a pull request and merge it. Under the `conventional_commit` release trigger, give it a title that releases nothing, such as `chore: move to a newer Workflow`.

Never move a repository while a worker runs or while a plan-approval transaction is unfinished. A job that straddles the move ends in `workflow_release_changed` and the next invocation decides again. Moving a repository in the middle of a milestone is proven only at a few phases; the full account is in [Workflow release changes](guide/troubleshooting.md#workflow_release_changed-and-workflow_release_changed); the quick fix is in [common problems](common-problems.md#a-step-refuses-with-workflow_release_changed).

## What you should see

After an update, `workflow-controller --version` prints the new version. After a Workflow move, `workflow-manager verify .` succeeds and `workflow-controller inspect .` names the new release as admitted.

## If it fails

- `inspect` refuses the repository as unsupported: the Controller does not admit that release. See [common problems](common-problems.md#the-repository-is-refused-as-unmanaged-or-unsupported-exit-20) and [compatibility](compatibility.md).
- A step refuses with `WORKFLOW_RELEASE_CHANGED`: the repository's release changed under a running step. Nothing was launched; run again.

Related: [install](install.md), [run](run.md), [release history](release-history.md).
