# CI2e: a runtime check replacing the static repo-reach heuristic

Item id: `workflow-controller-ci2e`.

Status: implemented on the milestone branch (four checkpoints); plan: [CONTROLLER_CI2E_PLAN.md](../ai-workflow/CONTROLLER_CI2E_PLAN.md) (revision 3). A `ci:` change that edits CI tooling, the generated workflows, tests and pages; it publishes no release. A slice of [CI2](workflow-controller-ci2.md), split at its planning. Depends on CI2. Source: [#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322); also [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: replace the CI1a static repo-reach heuristic in `tests/test_ci_changes.py` with a runtime check, for example a suite run with the tree unreadable, or a hash of the tree before and after, so a documentation-only classification is proven and not inferred from source text.

## What was done

- **`hide` removes the documentation set (CP1).** `python3 tools/ci_changes.py hide` deletes the tracked files `is_documentation` accepts and marks them `skip-worktree`, so `git status` stays clean; `--dry-run` lists; it refuses outside a Git work tree. Tests run it on real temporary repositories, including a reader module that fails after `hide` (the proof the check can fail).
- **The shards run blind (CP2).** The generated `tests.yml` `tests` job runs `hide` between the install and `exec-shard`; the `plan` job stays sighted. `tests.test_docs` and `tests.test_backlog` are `excluded` in `CI_PLACEMENT` (`docs-tests` runs them on the real checkout), which also removes their duplicate run.
- **The static scanner is gone (CP3).** `GATE_MODULES`, `GATE_TOOLS`, `MENTIONS`, `FIXTURES` and the AST scan in `tests/test_ci_changes.py` are deleted, with no second mechanism left.
- **The pages (CP4).** [Documentation-only pull requests](../guide/ci-and-releases.md#documentation-only-pull-requests) states the check, what it does not prove and how to reproduce it; [development](../guide/development.md) names the command.

## Limits

The check does not see Git-object reads, tolerant readers or passing subtests, and the `main`, package and conformance modules do not run blind (plan D5). The full shard set runs blind for the first time on the pull request's CI; a reader it finds moves its path out of the set (D3). Open decision O1 (also blind in the `main` and package jobs) stays with the owner and becomes a follow-up item if wanted. [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) stays open.

The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
