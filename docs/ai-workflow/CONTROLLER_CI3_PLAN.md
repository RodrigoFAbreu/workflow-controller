# CI3: align CI with the agreed standard: CI, Tests and Build and publish (Revision 3)

Work item: `workflow-controller-ci3`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `31663052b4e874d610ae8e49d08dd88d1f61e0db` ("chore: opt this repository in to the Controller's auto-merge (AO1) (#369)"), the tip of `main`, passed explicitly as `/milestone-plan 31663052b4e874d610ae8e49d08dd88d1f61e0db`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ci3` (`docs/backlog.yaml` order 94, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ci3.md`. It depends on nothing and is planned before CI2.
Released baseline: the latest tag. The change edits workflow files, their generator, tests and pages only, so the squash commit is a `ci:` and publishes no release (`ci` maps to `none` in the committed policy).
Pull request title: `ci: align the workflows with the agreed standard, CI, Tests and Build and publish (CI3)`

## Goal

The owner's agreed CI standard has three manually dispatchable entry points: **CI** (runs on pull requests and pushes to main, runs Tests first and Build and publish after success on main, and a manual CI dispatch from main also publishes), **Tests** (reusable, runs the tests and checks) and **Build and publish** (reusable, builds and publishes, never orchestrates tests). PR #357 already moved the repository most of the way. Three differences remain, and this item closes them without changing a required check name, a release rule or a test:

1. the reusable test workflow is named "Validate", the standard calls it "Tests";
2. `build-publish.yml` still validates and runs the after-merge checks itself when dispatched by hand, so test orchestration lives in two places;
3. nothing limits simultaneous runners to six.

## What exists

- **The model.** `tools/ci_workflows.py` generates `ci.yml`, `validate.yml`, `build-publish.yml`, `pr-title.yml` and `main-reuse-report.yml`; `python3 tools/ci_workflows.py --check` is the generation-drift check and `RETIRED_WORKFLOWS` lists files the generator no longer renders (their presence fails the check). `tests/test_ci_workflows.py` pins the models.
- **The required checks.** The ruleset requires `validate / plan`, `validate / tests-result`, `validate / package`, `workflow-conformance` and `PR title`. A called workflow's check is `<calling job id> / <called job id>`, so the names come from the job id `validate` in `ci.yml` and from `validate.yml`'s job ids, never from the called file's name or its `name:`. Renaming the file and the workflow name does not change them.
- **The shared workflows.** `docs-tests` already calls `RodrigoFAbreu/action-workflows/.github/workflows/python-tests.yml@main`. The rest of the shared set does not fit: `pr-title.yml` there only checks a Conventional Commit pattern, while this repository's `PR title` job runs `tools/release.py check-title` against the policy committed on the merge ref; `pr-check.yml` names its check "Conventional Commit title", not the required `PR title`; the build and publish workflows are Java and npm. Those stay custom (the standard's "keep custom validation in this repository when the shared workflows cannot cover it").
- **The runners.** On a pull request the `validate` call holds `changes`, `docs-tests`, `docs`, `plan`, six `tests (<i>)` shards, `tests-result` and `package`; beside it `workflow-conformance` (one job, managed by the Workflow Manager, never edited here) and `PR title` (seconds). On `main` the two `after-merge-*` jobs run beside `validate`, and `build-publish.yml`, when dispatched by hand, runs its own `validate` and after-merge jobs. The six shards alone are the guide's "runner budget"; with `package` and conformance a pull request holds up to eight.
- **Where the 27-minute PR CI stands.** The floor is the `acceptance_matrix` conformance atom (CI2's subject). This item does not try to shorten it.

## Decisions

- **D1. Rename by file and workflow name, never by job id.** `validate.yml` becomes `tests.yml`, workflow `name: Tests`; `ci.yml`'s calling job keeps the id `validate`, so every required check name is unchanged (a test pins the five names). The model's `VALIDATE_WORKFLOW` becomes `TESTS_WORKFLOW` and its `validate_workflow()` function becomes `tests_workflow()` (every caller in `tests/test_ci_workflows.py` and `tests/test_plan_document_consistency.py`'s `validate_job_ids()` and its docstrings and messages are updated with it); `validate.yml` joins `RETIRED_WORKFLOWS` (the check fails while it exists, like `main.yml`). Tests keeps `workflow_call` with the `tests-from-run` input and keeps `workflow_dispatch`. The ADRs and the completed-milestone records that name `validate.yml` are history and are not rewritten.
- **D2. Build and publish only builds and publishes.** Its self-validation (the `validate`, `after-merge-determinism` and `after-merge-mutations` jobs and the "no results handed in" branches of every condition) is removed. `build` and `publish` **always** require `inputs.validate-result == 'success'`; the coverage condition (the pushed commit's `after-merge-*` inputs, or for an ancestor the plan resumes the `release-target-*` results) is **added to** that requirement with `&&`, never offered as an alternative to it, so no input combination publishes without the caller's `validate-result`. `release-target-determinism` and `release-target-mutations` stay, because they check a commit the `release-plan` job picks, which only that workflow knows and the caller cannot orchestrate; they are gated on the same input (`inputs.validate-result == 'success'` joins their `if`), so they run only inside a run the caller already validated and a direct dispatch runs no test job at all. They are the one test run left in the workflow and the page says why.
- **D3. A manual publish is a CI dispatch from main.** `ci.yml` already runs `validate`, the after-merge checks and `build-publish` for a `workflow_dispatch` on `main` (its `build-publish` condition is "not a pull request, on main, validate succeeded") and never reuses another run's results on a dispatch. A test pins exactly that, and pins that a dispatch from another branch publishes nothing. Build and publish keeps `workflow_dispatch` (the standard's three entry points are all dispatchable) but a direct dispatch hands it null inputs: `release-plan` (a read-only classification, no test) writes a notice naming the CI dispatch, and `release-target-*`, `build` and `publish` do not run, whatever state `release-plan` reports, `RESUME` of an ancestor included (D2's input gate). A direct dispatch therefore runs no test job and can never publish. With no self-run jobs left, dropping the `main-release` slot from its concurrency group (it is `build-publish-<run id>`) cannot put two whole-catalogue checks beside a `CI` run.
- **D4. The runner cap is one number and one proof.** `RUNNER_LIMIT = 6` in the model. Mechanisms, no more: (a) **always** emit `max-parallel: TEST_MAX_PARALLEL` on the shard matrix, whatever option O1 picks, so the cap on shard runners holds whatever the planner returns, the exclusive shard included, and the model states a size for a matrix whose size is only known at run time (`shard: ${{ fromJSON(needs.plan.outputs.shards) }}`); (b) under O1's options B and C, also lower `CI_MAX_SHARDS` in `tools/test_shards.py` (the planner's ceiling, line 213) to the same number, so fewer shards are planned rather than queued, which changes the planner's constant, the tests that pin it and the guide sentences that quote it (CP3 and CP4 name them); (c) a `needs` chain so that `docs-tests`, `docs` and `package` hold one runner at a time (one lane beside the shards), and (d) ordering the two `after-merge-*` jobs behind `validate` (they stop running beside the shards; the release waits for them already, so only the release's latency grows). Under option A the planner does not change; under B and C only `CI_MAX_SHARDS` does, no other planner logic.

  **The two numbers are tied by a test, never repeated.** The model does not import the planner. The CP3 test reads `CI_MAX_SHARDS` from `tools/test_shards.py` and asserts `TEST_MAX_PARALLEL <= CI_MAX_SHARDS` (equal under B and C, lower under A), and that the shard matrix of every generated workflow carries `max-parallel` (a matrix with none fails the test, so an unbounded matrix cannot be added silently). The matrix counts `max-parallel`, which already includes the exclusive shard that `build_plan` adds when `EXCLUSIVE_ATOMS` (empty today) registers an atom, because the cap limits concurrent jobs, not the planned count.

  **How each node is sized.** A static job counts one, a matrix with `max-parallel` counts that number, and `docs-tests`, a call to `RodrigoFAbreu/action-workflows/.github/workflows/python-tests.yml@main`, counts **one runner**. That is an assumption about a workflow on a mutable ref this repository does not pin and the test cannot read offline (it has one job, `python`, today); the test pins the `uses` value so a change of the called workflow is a visible edit, and the page says the assumption.

  A test computes the worst case from the generated models **with no timing assumption at all**: it flattens each workflow's called workflows into one job graph, takes the heaviest set of jobs with no `needs` path between them, and adds, **for every event and every `CI_MAIN_REUSE` mode**, the runners outside that graph, each counted against the whole set and never "because it ends quickly": one for `workflow-conformance` (the test asserts that file has exactly one job, so a managed change that adds one fails here), one for `PR title` on `pull_request` (`pr-title.yml` also runs on `edited` and `reopened`, so it can start beside the shards), and one for `main-reuse-report.yml`'s `reuse-report` job on `push` to `main` when `CI_MAIN_REUSE` is not `on` or `off` (unset, `report` or any other value; it is read from the model's `if`, so the test iterates `on`, `off`, unset and `report` and asserts the job is counted exactly where its condition holds). With the limit at six and these two outside runners (conformance plus `PR title` on a pull request, conformance plus `reuse-report` on `main`) the generated shards plus the one `docs-tests`/`docs`/`package` lane may use at most four runners: three shards, or four with the lane ordered before or after them (O1).

  **The lane never loses a required check.** `package` is the required `validate / package` check, and GitHub treats a skipped required check as satisfied, so the chain must not let an upstream result skip it. `package` therefore gets `needs: [docs]` (option C after the shards: `[docs, tests]`) and `if: !cancelled()`: it runs whatever its predecessors' results, documentation-only pull requests included (where `tests` is skipped), and is skipped only when the run is cancelled. `docs-tests` and `docs` keep today's conditions (`docs` needs `docs-tests` under the implicit `success()`), and `tests-result` keeps reading `docs` fail-closed as it does. The `if` of every job the reordering touches is written in the model and read by the test.
- **D5. The cap is per run, and that is stated.** GitHub has no cross-run runner limit in workflow files, and a repository-wide concurrency group would cancel all but one pending run. The cap holds for one run beside the managed conformance job; several open pull requests, or a pull request beside a `main` run, add up, which the guide says in one sentence (the Controller opens one milestone pull request at a time).

## Open decisions

No repository decision-record "Open decision" row is touched. One owner decision remains and it changes the pull request's wall time, so the plan states the numbers and does not choose silently.

- **O1. What "six per repository" costs in wall time (owner decision, recorded in the item page before CP3 starts).** `python3 tools/run_tests.py plan --profile ci --ci-placement` at the base commit gives six shards that are balanced (1630.03 s each, about 27 minutes; about 163 minutes of work in all). The planner estimate is conservative: the guide's measured six-shard run (run 39806086354) had shards of 11.0 to 19.0 minutes and a 19.3-minute pull request. The estimates below use the planner's numbers (setup and `tests-result` add a few minutes to each) and the measured shard range in brackets; CP3 records the pull request's own measured run.

  | option | settings | shards in flight | planner estimate | against the 15-20 minute target |
  | --- | --- | --- | --- | --- |
  | A | 6 shards, `TEST_MAX_PARALLEL = 3` | 3, in two full waves | about 54 minutes (about 38 measured-based) | about 2.5 to 3 times over |
  | B | `CI_MAX_SHARDS = 3`, 3 shards | 3, one wave | about 54 minutes (same total work over fewer runners; fewer setups, so fewer runner-minutes than A) | about 2.5 to 3 times over |
  | C | `CI_MAX_SHARDS = 4`, the `docs-tests`/`docs`/`package` lane ordered before or after the shards | 4 | about 41 minutes plus that lane's own duration (measured at CP3) | about 2 times over |
  | D | the owner raises `RUNNER_LIMIT` (for example to 9: 6 shards, the lane, conformance and `PR title`) | 6 | about 27 minutes (the current figure) | over, CI2's subject |

  Every option that honours six doubles the pull request's checks or more; only D keeps them. The provisional choice, B, is the one with the least change (one constant, no reordering) and the same time as A at a lower runner-minute cost, and it is chosen only to let the plan be approved: **CP3 must not start until the owner's choice among A to D is recorded in `docs/items/workflow-controller-ci3.md`** (the CP3 implementer, not the Controller, which has no mechanism that reads a plan's prose and pauses before a checkpoint and whose plan gate is automatic, asks the owner through SignalHub when CP3 starts and stops until the answer is recorded in that page; CP1 and CP2 proceed meanwhile), and if the owner picks C or D the constants and the test's expected counts change with it. CI2 (the 20-minute target) is planned after this item and starts from the measured result, which CP3 records.

## Checkpoints

<!-- CP1 -->
### CP1: Tests, the renamed reusable workflow

In `tools/ci_workflows.py` rename the model's workflow to Tests (file `tests.yml`, `name: Tests`, header comment), `VALIDATE_WORKFLOW` to `TESTS_WORKFLOW`, and add `validate.yml` to `RETIRED_WORKFLOWS` (D1). Regenerate with `python3 tools/ci_workflows.py --write` (the new `tests.yml`, a changed `ci.yml` and `build-publish.yml`, and `validate.yml` removed). Update `tests/test_ci_workflows.py` (every reference to the file and the workflow name; the five required check names, the job id `validate`, the retired list), the three docstrings that name the file in `tools/ci_changes.py` and `tools/ci_reuse.py`, the sample path in `tests/test_ci_changes.py`, and the renamed `tests_workflow()` (D1) with its uses in `tests/test_plan_document_consistency.py` (`validate_job_ids()`, `readme_validate_job_violations`, their docstrings and messages that name `validate.yml`).

**Files:** `tools/ci_workflows.py`, `.github/workflows/tests.yml`, `.github/workflows/validate.yml`, `.github/workflows/ci.yml`, `.github/workflows/build-publish.yml`, `tools/ci_changes.py`, `tools/ci_reuse.py`, `tests/test_ci_workflows.py`, `tests/test_ci_changes.py`, `tests/test_plan_document_consistency.py`

**Tests:** `tests.test_ci_workflows`, `tests.test_ci_changes`, `tests.test_ci_reuse`, `tests.test_plan_document_consistency`, `python3 tools/ci_workflows.py --check`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: Build and publish only builds and publishes

Remove the self-validation from `build_publish_workflow` in `tools/ci_workflows.py` (D2): the three self-run jobs, the `SELF_VALIDATING` branches and the `main-release` slot in its concurrency group (a called run takes its caller's; its group is now `build-publish-<run id>`), gate `release-target-*` on `inputs.validate-result == 'success'`, make `build` and `publish` require that input in every branch of their conditions (D2), and add the `release-plan` notice for a run handed no results (D3). Regenerate. In `tests/test_ci_workflows.py` replace the manual-dispatch tests of `build-publish.yml` with: no job named `validate` or `after-merge-*` there; every `build` and `publish` condition contains `inputs.validate-result == 'success'` as a conjunct (parsed, not substring-matched) and names only the inputs and `release-target-*`; `release-target-*` carry the same conjunct; evaluating the generated conditions over every input a direct dispatch can produce (null or empty strings) and every `release-plan` state (`RESUME` of an ancestor and `RELEASE_DUE` included) leaves `release-target-*`, `build` and `publish` all not run; a `ci.yml` dispatch from `main` reaches `build-publish` with `validate` and the after-merge results, and from another branch does not (D3).

**Files:** `tools/ci_workflows.py`, `.github/workflows/build-publish.yml`, `tests/test_ci_workflows.py`

**Tests:** `tests.test_ci_workflows`, `tests.test_release_tools`, `python3 tools/ci_workflows.py --check`.
<!-- /CP2 -->

<!-- CP3 -->
### CP3: the six-runner limit and its proof

Start from the owner's recorded choice (O1). Add `RUNNER_LIMIT` and `TEST_MAX_PARALLEL` to the model and always emit `max-parallel` in the shard strategy (D4). Under B or C also lower `CI_MAX_SHARDS` in `tools/test_shards.py` to `TEST_MAX_PARALLEL` and update its pins: `tests/test_test_shards.py` (the `(ci.target_shard_seconds, ci.min_shards, ci.max_shards) == (180, 2, 6)` tuple and the `shards.CI_MAX_SHARDS == 6` assertion) and `tests/test_scenarios.py` (the CI plan built with the ceiling); under A neither file changes. Where O1 needs it, order the `docs-tests`/`docs`/`package` lane before or after the shards. Give `package` `needs` on `docs` (and `tests` under C) and `if: !cancelled()` (D4), and make the two after-merge jobs of `ci.yml` wait for `validate` (their `if` becomes "not cancelled, not a pull request, `validate` succeeded"). Regenerate. In `tests/test_ci_workflows.py` add the worst-case calculation of D4 as a test helper (the flattened job graph, the maximum-weight set of jobs with no `needs` path between any two, found by a small search that prunes on that relation) and assert, for `pull_request` (with `PR title` counted against the whole set, since an `edited` or `reopened` event starts it beside the shards), `push` to main under each `CI_MAIN_REUSE` mode (`on`, `off`, unset, `report`) with `main-reuse-report.yml`'s `reuse-report` counted wherever its `if` holds, and each of the three dispatchable workflows' `workflow_dispatch` (`ci.yml` on `main` and on another branch, `tests.yml` alone, and `build-publish.yml`, which after CP2 runs only `release-plan`), that the total with the outside runners is at most `RUNNER_LIMIT`, and that no outside runner is excused by how long it runs. Also assert: every shard matrix carries `max-parallel` and `TEST_MAX_PARALLEL <= CI_MAX_SHARDS` read from the planner module (I1/I2), `docs-tests` counts one runner and its `uses` value is pinned, `workflow-conformance.yml` has exactly one job, the required check names are unchanged, and `package` is not skipped for any upstream result (`success`, `failure`, `cancelled`, `skipped` of `docs`, and of `tests` under C) on `pull_request`, `push` and `workflow_dispatch`, a documentation-only pull request (where `tests` is skipped) included, by evaluating its generated `if` (I3). Record the pull request's measured run in the item page at CP4.

**Files:** `tools/ci_workflows.py`, `.github/workflows/tests.yml`, `.github/workflows/ci.yml`, `tests/test_ci_workflows.py`; under B or C also `tools/test_shards.py`, `tests/test_test_shards.py`, `tests/test_scenarios.py`

**Tests:** `tests.test_ci_workflows`, `tests.test_ci_changes`, `tests.test_ci_reuse`, `tests.test_package_structure`, `python3 tools/ci_workflows.py --check`; under B or C also `tests.test_test_shards` and `tests.test_scenarios`.
<!-- /CP3 -->

<!-- CP4 -->
### CP4: the pages and the item record

`docs/guide/ci-and-releases.md` (the tree and the paragraph that call the reusable workflow "Validate" and say a dispatched Build and publish validates itself; the runner-budget paragraph and the 8-runner statements, now the cap of D4 with its per-run limit of D5; the manual-publish route is a CI dispatch from `main`; the release-latency paragraph, which now adds the `after-merge-*` duration (about 15 to 17 minutes) to every ordinary release because they run after `validate` instead of beside it, stated as a number), `docs/guide/development.md` (the line naming `validate.yml`; under B or C also the shard-ceiling sentence "a ceiling of six shards (`CI_MAX_SHARDS`; a seventh is the owner's runner-budget decision ...)"), and, under B or C, the "at most 6 shards (`CI_MAX_SHARDS` ...)" sentence of `ci-and-releases.md`; the page also states that the `docs-tests` runner count assumes a called workflow on a mutable ref, `README.md` (its workflow names), and `docs/items/workflow-controller-ci3.md` (status, plan link, what was done, O1 with the owner's recorded choice and the table's numbers, the measured pull request run, and the correction of its sentence "The 8-shard plan and the after-merge jobs currently exceed six": CI runs six shards, set by `CI_MAX_SHARDS` before this item). The ADRs and archived plans that name `validate.yml` stay as history.

**Files:** `docs/guide/ci-and-releases.md`, `docs/guide/development.md`, `README.md`, `docs/items/workflow-controller-ci3.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-ci3`.
<!-- /CP4 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | The reusable test workflow is `tests.yml`, named Tests, and every required check name is unchanged | CP1 |
| R2 | Build and publish orchestrates no tests of the pushed commit, always requires `validate-result == 'success'` to build or publish, a manual CI dispatch from main publishes, and a direct dispatch runs no test job and cannot publish | CP2 |
| R3 | One run holds at most six runners beside the managed conformance job, `PR title` and the main reuse report, proved from the generated models for every event and `CI_MAIN_REUSE` mode; the wall-time cost is the owner's recorded choice | CP3 |
| R4 | The pages and the item record state the standard, the cap with its per-run limit and the measured result | CP4 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Tests, the renamed reusable workflow | - | 1 | 1 |
| CP2 | Build and publish only builds and publishes | CP1 | 2 | 1 |
| CP3 | The six-runner limit and its proof | CP2 | 2 | 1 |
| CP4 | The pages and the item record | CP3 | 1 | 1 |
