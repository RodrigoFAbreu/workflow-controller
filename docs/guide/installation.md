# Installing, upgrading and rolling back

[Back to the documentation map](../README.md)

- [Requirements](#requirements)
- [Install a release (recommended)](#install-a-release-recommended)
- [Upgrade](#upgrade)
- [Roll back](#roll-back)
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
