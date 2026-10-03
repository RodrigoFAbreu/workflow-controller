# Install the Controller

> For: anyone installing the Controller on a machine. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

Goal: a verified `workflow-controller` on your `PATH`, installed from a release wheel.

## Prerequisites

- Python 3.12 or newer and [pipx](https://pipx.pypa.io/).
- `git` 2.31 or newer. An older Git refuses a Workflow 2.6.0 or protocol-mode target.
- The `claude` CLI: every [worker](glossary.md#worker) is a `claude` session.
- `workflow-manager`, from the [Workflow Manager repository](https://github.com/RodrigoFAbreu/workflow-manager#readme). The Controller asks it whether a [target](glossary.md#target)'s Workflow installation is sound before it does anything.
- `gh`, authenticated, only for a repository that commits a `.workflow-controller/policy.json` (milestone branches, pull requests and releases).

The Controller finds Workflow Manager through `--workflow-manager`, then the `WORKFLOW_CONTROLLER_WORKFLOW_MANAGER` environment variable, then `workflow-manager` on `PATH`. The first one that is set is final.

## Steps

1. Pick the release and download its two files. Every release at <https://github.com/RodrigoFAbreu/workflow-controller/releases> carries the wheel and `SHA256SUMS`. Replace `<version>` with the release you want, for example one listed in [release history](release-history.md).

   ```bash
   VERSION=<version>
   BASE=https://github.com/RodrigoFAbreu/workflow-controller/releases/download/v$VERSION
   curl -fLO "$BASE/workflow_controller-$VERSION-py3-none-any.whl"
   curl -fLO "$BASE/SHA256SUMS"
   ```

   With `gh`, one command does both: `gh release download v$VERSION -R RodrigoFAbreu/workflow-controller`.

2. Check the wheel against the checksums.

   ```bash
   sha256sum -c SHA256SUMS
   ```

3. Install it with pipx.

   ```bash
   pipx install ./workflow_controller-$VERSION-py3-none-any.whl
   ```

4. Check what is running.

   ```bash
   workflow-controller --version
   workflow-controller status
   ```

## What you should see

`sha256sum -c` prints `workflow_controller-<version>-py3-none-any.whl: OK`. `--version` prints two lines: `workflow-controller <version>`, then `runtime: package (release v<version>; built from <commit>; package <digest>)`. `status` on a new machine reports no Controller runtime state and `active: none`.

The checksum is the proof that a wheel is the release; the word "release" in `--version` comes from the build and proves nothing about origin by itself. See [Runtime identity](guide/runtime.md#runtime-identity).

## The settings file

You do not need one. The Controller reads an optional user settings file and, when `step`, `run`, `resume` or `milestone-binding` writes, fills in any missing default. `workflow-controller settings path` prints where it is and `workflow-controller settings show` prints every value with its source. The keys, their bounds and the order in which a value is chosen are described once, in [The settings file](guide/runtime.md#the-settings-file).

```bash
workflow-controller settings path
workflow-controller settings show
```

## Uninstall

```bash
pipx uninstall workflow-controller
```

This removes the program only. Job records, run logs and milestone binding records stay under the runtime root, by default `~/.local/state/workflow-controller`. Keep them while a milestone is in flight; the binding records are read by lifecycle decisions ([Runtime state](guide/runtime.md#controller-owned-runtime-state)).

## Install from a checkout

Only to test unreleased code. See [Development and the test runner](guide/development.md).

## If it fails

- `sha256sum` reports a mismatch: delete both files and download again. Do not install a wheel that does not match.
- `workflow-controller` is not found after pipx finished: run `pipx ensurepath` and open a new shell.
- The Controller refuses a repository: see [Troubleshooting](guide/troubleshooting.md#the-repository-is-refused-as-unmanaged-or-unsupported), and [compatibility](compatibility.md) for which Workflow releases a Controller admits.

Next: [run it on a repository](run.md). To change versions later, see [update and roll back](update.md).
