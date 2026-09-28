# Installing, upgrading and rolling back

[Back to the documentation map](../README.md)

- [Requirements](#requirements)
- [Supported Workflow releases](#supported-workflow-releases)
- [Install a release (recommended)](#install-a-release-recommended)
- [Upgrade](#upgrade)
- [Roll back](#roll-back)
- [Moving a target to another Workflow release](#moving-a-target-to-another-workflow-release)
- [Install from a checkout](#install-from-a-checkout)
- [Uninstall](#uninstall)

## Requirements

| Needed | Why |
|---|---|
| Python 3.12 or newer | `requires-python = ">=3.12"` |
| [pipx](https://pipx.pypa.io/) | installs the Controller into its own virtual environment, so it never runs from a checkout |
| `git` | every target repository is a Git worktree |
| `claude` (Claude Code CLI) | every worker is a `claude` session; `--claude-binary` points at a different one |
| `workflow-manager` | the Controller asks it whether a target's Workflow installation is sound before doing anything |
| `gh`, authenticated | only for repositories with a `.workflow-controller/policy.json` (milestone branches, pull requests, releases) |

The Controller finds Workflow Manager through `--workflow-manager`, then the
`WORKFLOW_CONTROLLER_WORKFLOW_MANAGER` environment variable, then
`workflow-manager` on `PATH`. The first of these that is set is final: if it
does not resolve to an executable, the Controller refuses rather than trying
the next.

## Supported Workflow releases

A target is admitted only when Workflow Manager verifies its installation and
its `.workflow-manager/installation.json` names a Workflow release the
Controller has been validated against
(`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES`):

| Controller | Workflow releases admitted |
|---|---|
| 1.3.0 and later | 2.5.1, 2.6.0 |
| 1.2.1 and earlier | 2.5.1 |

Admission is by exact release, so 2.5.0 and 2.6.1 are refused as not
validated, and 2.7.0 as outside the supported lines
([Troubleshooting](troubleshooting.md#the-repository-is-refused-as-unmanaged-or-unsupported)).
`workflow-controller inspect <repo>` prints the release it admitted.

A 2.5.1 target behaves exactly as it did under 1.2.1. For a 2.6.0 target the
Controller asks Workflow's own queries for the feedback path and for whether
a plan-review bundle is current
([Workflow's queries](automation.md#workflows-queries-260-and-later)). The
design record is
[ADR 0006](../adr/0006-workflow-release-admission-and-per-release-contracts.md).

## Install a release (recommended)

Every release is a GitHub Release at
<https://github.com/RodrigoFAbreu/workflow-controller/releases> carrying two
files: the wheel, `workflow_controller-<version>-py3-none-any.whl`, and
`SHA256SUMS`. Install the wheel with pipx, after checking it:

```bash
VERSION=1.2.1   # the release you want; the releases page lists them
BASE=https://github.com/RodrigoFAbreu/workflow-controller/releases/download/v$VERSION

curl -fLO "$BASE/workflow_controller-$VERSION-py3-none-any.whl"
curl -fLO "$BASE/SHA256SUMS"
sha256sum -c SHA256SUMS          # must print: ... OK
pipx install ./workflow_controller-$VERSION-py3-none-any.whl
workflow-controller --version
```

With `gh`, the two downloads are one command:
`gh release download v$VERSION -R RodrigoFAbreu/workflow-controller`.

`--version` prints two lines. The first is always
`workflow-controller <version>`; the second says what is running. For a
release it reads
`runtime: package (release v1.2.1; built from <commit>; package <digest>)`
(see [Runtime identity](runtime.md#runtime-identity)).

The checksum is the proof that a wheel is the release. The "release" label in
`--version` comes from the build and is not proof of origin on its own.

## Upgrade

1. Check that nothing is running: `workflow-controller status` must show
   `active: none`.
2. Download and verify the new release as above, then install it over the old
   one:

   ```bash
   pipx install --force ./workflow_controller-<new version>-py3-none-any.whl
   workflow-controller --version
   ```

What an upgrade does to a Controller that is already running:

- A running `step` or `run` executes from its own snapshot of the installed
  package, so the new files never reach it.
- A new release with the same generation (`controller/GENERATION.json`) is
  ignored by a running `run`, which finishes on the old version.
- A new generation stops a running `run` at its next orchestration boundary
  with exit `50` and a handoff record, the same as a newer committed
  generation does for a source checkout. Rerun `run` with the new version.
- Two cases fail closed instead. Both surface as `SourceSnapshotError` (exit
  `20`, `raised_by: "detect"`), not `50`, and in both you simply rerun `run`:
  - `pipx install --force` deletes and recreates the venv, so a `run` whose
    boundary check lands in that window finds no installed package;
  - an upgrade that changes Python's minor version moves the `site-packages`
    path the snapshot recorded as `origin_source_root`, so the installed
    package is no longer where the run looks for it.

Upgrade between jobs, not during one.

## Roll back

A rollback is the same command with an older wheel:
`pipx install --force <older wheel>`. Going back across a generation has two
consequences:

- a job record written by the newer generation is refused as
  `StaleJobRecordError` by the older one, and `resume --abandon` refuses it
  too. Reinstall the newer version and let its own `resume` clear the record,
  then roll back. Running `resume` before the rollback avoids this;
- a `run` still executing the newer generation raises
  `GenerationHandoffPendingError` (exit `20`) at its next boundary, since the
  installed generation is now older than the running one.

Every release so far is generation 1.

## Moving a target to another Workflow release

A target moves to a new Workflow release through Workflow Manager, never by
hand, and the Controller must admit the new release first: install
Controller 1.3.0 or later before moving a target to 2.6.0. Workflow
Manager's `update` replaces the managed files and rewrites
`installation.json`. It commits nothing, reads no Workflow state and
migrates nothing; Workflow 2.6.0's own scripts handle work items created
under 2.5.1.

**Best between milestones**, with no work item in flight and nothing
running (`workflow-controller status` shows `active: none`). On a
repository with milestone branches, do it on a branch of its own and merge
it like any other change:

```bash
git switch -c chore/workflow-2.6.0 main
workflow-manager --release-version 2.6.0 update .
workflow-manager verify .
workflow-controller inspect .       # must admit 2.6.0
git status                          # only the files the Manager wrote
```

Commit exactly what the Manager wrote, then open a pull request and merge
it. With no version bump, the merge classifies `NO_CHANGE` and publishes no
Controller release.

**In flight**, updating the milestone's own branch in place is proven only
at these phases:

- `AWAITING_LOCAL_PLAN_REVIEW`, `REVISING_PLAN` and
  `AWAITING_PLAN_APPROVAL`, including a bundle a 2.5.1 withdrawal left
  behind (the Controller stops at a gate whose steps regenerate it);
- `IMPLEMENTING`, between checkpoints.

At any other phase an in-flight update is untested. Whatever the phase:

- **never with a plan-approval journal in flight** (an `/approve-review plan`
  that has not finished): Workflow says to finish or abandon it under 2.5.1
  first;
- **never while a worker runs.** A job that straddles the update fails
  closed at verification (`workflow_release_changed`), and the next
  invocation admits the new release and decides again;
- after the update, `/milestone-plan` at `IMPLEMENTING` no longer re-plans:
  2.6.0 routes a plan change to `/request-plan-amendment`, which only a human
  runs.

Updating the trunk while a milestone branch stays on the old release also
works: on the branch the Controller keeps the branch's release, readiness
still ends at `integration_required`, and after the manual merge the
close-out completes and the same step then refuses once with
`WORKFLOW_RELEASE_CHANGED` (the close-out is recorded). The next invocation
admits the trunk's release. Workflow 2.6.0's lifecycle lock reads the
`installation.json` committed at `HEAD` of every registered worktree, so a
linked worktree whose branch predates the update counts as lagging; the
Controller itself creates no linked worktrees.

Rolling the Controller back to 1.2.1 refuses a target already on 2.6.0
(`UNSUPPORTED_WORKFLOW_VERSION`, exit `20`); see [Roll back](#roll-back).

## Install from a checkout

Prefer a release. Build from a checkout only to test unreleased code, or when
no release is reachable. There are two ways, and they give different runtimes.

**A local wheel (a `package` runtime, like a release).** Build the wheel and
install it with pipx:

```bash
git clone https://github.com/RodrigoFAbreu/workflow-controller.git
cd workflow-controller
python3 -m pip wheel --no-deps -w dist .
pipx install --force ./dist/workflow_controller-*-py3-none-any.whl
workflow-controller --version    # runtime: package (local build from <commit>)
```

The build records the checkout's commit, whether it had uncommitted changes,
and a digest of the package. A wheel built from uncommitted changes, or with
no verifiable provenance, needs `--allow-dirty-source` to run `step`, `run`
or `resume`. A local build refuses a stale `build/` directory left by an
earlier build that held files this one does not; delete `build/` and rebuild.
A plain `pip install .` also gives a `package` runtime.

**An editable install (a `source` runtime, for development).**

```bash
pip install -e .
```

An editable install runs the checkout itself. `step`, `run` and `resume`
snapshot the checkout's committed `HEAD` with `git archive`, so what runs is
always a commit. With uncommitted changes to the Controller's own files they
refuse (`DirtyControllerSourceError`) unless you pass `--allow-dirty-source`,
which snapshots the working tree instead. A source runtime keeps its state in
`<checkout>/.controller/` (row 3 of the
[runtime root ladder](runtime.md#controller-owned-runtime-state)).

## Uninstall

```bash
pipx uninstall workflow-controller
```

This removes the program only. The Controller's own state (job records, run
logs, milestone binding records) stays under its runtime root, by default
`~/.local/state/workflow-controller`. The milestone binding records under
`repositories/` are read by lifecycle decisions: keep them while a milestone
is in flight (see [Runtime state](runtime.md#controller-owned-runtime-state)).
