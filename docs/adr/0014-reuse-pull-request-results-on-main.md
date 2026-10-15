# ADR 0014: Reuse a pull request's validated results on `main` for the identical tree

Status: accepted (2026-10-15). Amends the validation decisions of
[ADR 0003](0003-trunk-branch-pr-release-orchestration.md) (every push to
`main` is validated by `validate.yml` before it can release) and the
run-time proof of [ADR 0005](0005-adaptive-test-sharding.md) (every planned
test runs exactly once per event). The code is `tools/ci_reuse.py`, the model
in `tools/ci_workflows.py` and the generated `main.yml` and `validate.yml`.
The operator's view is
[Reusing a pull request's results on `main`](../guide/ci-and-releases.md#reusing-a-pull-requests-results-on-main-ci_main_reuse).

## Context

Every change reaches `main` through a pull request whose CI ran the full
validation. A squash (or merge-commit) merge creates a new commit, but its
tree equals the tree of the pull request's merge ref whenever the base did
not move after the last CI run. `Main` then ran the same tests on the same
files again: about 55 runner-minutes and 18-20 minutes of wall time, for a
release decision that is made on the tree.

## Decisions

1. **Reuse is decided on the tree, not on the commit.** `main` may skip the
   shards only when the `timings-ci-attempt-<a>` artifact of the merged pull
   request's latest successful `CI` run records a tree equal to
   `git rev-parse HEAD^{tree}` of the pushed commit **and** a plan digest
   equal to the plan computed for that commit. `tests-result` of every run
   now writes the tree it validated into that artifact (`tree.txt`).
2. **One variable is the switch, and the default changes nothing.**
   `CI_MAIN_REUSE`: unset or `report` computes and logs the decision while the
   full validation runs; `on` reuses; `off` and any other value do nothing.
   Going back is a variable change. It ships report-only, and is switched on
   by the owner after at least three merges whose reports were cross-checked
   against the full run.
3. **Report mode costs no wall time.** `reuse` (which `validate` waits for)
   runs only for `on`. For unset, `report` and unknown values a separate
   `reuse-report` job runs beside `validate`; nothing needs it and it is
   `continue-on-error`. `off` runs neither.
4. **Fail closed, twice.** The `reuse` job (read-only: `actions`, `contents`
   and `pull-requests` read) passes a run id only when every check passes. A
   failed lookup, a missing artifact, an artifact from before reuse, an
   ambiguous pull request, a fork, a rebase merge, a moved base,
   `workflow_dispatch` or a non-`main` ref passes none, so the shards run. A
   crashed `reuse` job also passes none (`validate` runs with `!cancelled()`).
   `tests-result` then verifies the tree and digest again, from the downloaded
   artifact, and fails the run on any mismatch (no release), then re-runs
   `aggregate` on the recorded records: each planned test exactly once, all
   passed, no leaked process. A reuse never turns a failure into a pass.
5. **Nothing else weakens.** Pull request runs are unchanged (the input is
   empty, so the shards run). `release-plan`, `build` and `publish` keep
   `needs: validate`. The `package` job always runs, on `main`'s own commit.
   The managed conformance workflow is untouched. The caller jobs
   (`ci.yml`, `main.yml`) grant `validate.yml` `actions: read`, because a
   called workflow cannot hold more than its caller; only `tests-result` uses
   it, to read the other run's artifact.

## What is given up

With `on`, a tree is executed once, on the pull request. The second execution
on `main` would only add value against:

- a **flaky pass**: a test that passed by chance on the pull request is now
  trusted for the release;
- **runner-image drift**: the image may change between the pull request's run
  and the merge;
- **time-dependent** or environment-dependent tests.

The fixes that closed earlier flakes (ADR 0005 amendments) are not weakened,
and a failed `Main` is still re-runnable with `CI_MAIN_REUSE` set to `off` or
`report`, which runs the full shards on the same commit.

## Alternatives rejected

- **Reuse by commit or by pull request head.** A squash commit is new and the
  head is not the merged tree; only the tree is the thing that was tested.
- **Always skip on `main` (no switch).** Not reversible without a code
  change, and not cross-checkable first.
- **Fall back inside `tests-result` when the tree differs.** A fallback would
  need the shards to exist in the same run; the `reuse` job decides before
  `validate`, so a mismatch found later fails closed instead.
