# CI2b: run each test once on a pull request, the duplicate-run removal (Revision 2)

Work item: `workflow-controller-ci2b`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `e30d790d4067af96d0b06127b1416754fc730e1d` ("ci: refresh the shard timings and measure the acceptance-matrix contention (CI2) (#373)"), the tip of `main`, passed explicitly as `/milestone-plan e30d790d4067af96d0b06127b1416754fc730e1d`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ci2b` (`docs/backlog.yaml` order 96, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ci2b.md`. It depends on `workflow-controller-ci2`, which is accepted.
Released baseline: the latest tag. The change edits the committed test placement, tests and pages only, so the squash commit is a `ci:` and publishes no release (`ci` maps to `none` in the committed policy).
Pull request title: `ci: run each test once on a pull request, the conformance suites and the functional flows (CI2b)`

## Goal

Stop running the same test twice on a pull request, with one mechanism: the committed CI placement (`CI_PLACEMENT` in `tools/test_shards.py`), which already moves whole modules out of the shard matrix (to the package job, after the merge, or out of CI). Two duplicates are removed:

1. **The frozen conformance suites.** The nine suites the Workflow Manager installs run in the managed `workflow-conformance` job (a required check, on every pull request and every push to `main`) and again as the `conformance:` atoms inside the shards. Their atoms total 2,271 s in the committed timings, against the three shards' 10,482 s of estimate (3 x 3,494 s), and the planner also charges a contention factor of 1.15 on a shard that runs both lanes.
2. **The four functional flows.** `tests/test_functional_lifecycle.py` (four tests, 691 s in the committed timings) and the four `Scenario_scenario_functional_*` scenarios of `tests/test_scenarios.py` (640 s) run the same four flow bodies, once with `unittest` assertions and once inside the scenario kit's sandbox.

The expected saving is read off the planner, not promised: CP3 rescores the base plan with the shrunken inventory (the method CI2 used) and records the number. [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target) stays open: this item does not close it.

## What exists

- **The placement.** `CI_PLACEMENT` is keyed by the module an atom belongs to and takes `PACKAGE`, `MAIN` or `EXCLUDED`. `ci_partition` splits the inventory into `shards`, `main`, `package` and `excluded`; `python3 tools/run_tests.py plan --profile ci --ci-placement` plans only the `shards` part, and `tests-result`'s run-time coverage proof ("all planned tests ran once", `aggregate`) is over exactly that plan. A local full run (`discover`, no `--ci-placement`) keeps every module, so an excluded module still runs on a developer's machine. A placed module the inventory does not contain refuses (`ci_partition` raises `InventoryError`), so a stale entry cannot go unnoticed.
- **The conformance family.** Its atoms are `conformance:<file>` ids read from the managed `workflow-conformance.yml`'s `suites=` list, and every one has the module key `conformance` (`conformance_family`), so one placement entry moves all nine, and a Workflow update that adds or drops a suite changes the family with no edit here.
- **The conformance lane.** A shard that holds conformance atoms runs them in a second process beside its controller tests (`LANE_CONTENTION`, `assign_lanes`). With none planned a shard has no lane, so the planner code is unchanged; local plans keep the lane.
- **The flows.** The four bodies are `flow_cycle_closes`, `flow_stops_for_a_person`, `flow_stops_before_or_without_a_run` and `flow_isolation_holds` in `tests/test_functional_lifecycle.py`. `tests/scenarios/lifecycle.py` imports that module (`tfl`) and calls the same bodies with the kit's judge and a `mutation` argument, which is how the after-merge negative suite (`tests.test_scenarios_negative`, placed `MAIN`) breaks each claim once. So the scenario copy is the one the catalogue, the determinism check and the mutations already depend on. The `unittest` copy adds one thing for the isolation flow only: `IsolationTest` wraps `flow_isolation_holds` in `short_folders_made()` (`tests/test_functional_lifecycle.py`) and asserts that the run built a sandbox and left no short `TMPDIR` folder behind (the C12a leftover check). Those folders are made by `controller/functional_review.make_short_tmp` under `$XDG_RUNTIME_DIR` or `/tmp`, outside the kit's scratch tree, so `functional_tester_isolation` (`tests/scenarios/lifecycle.py`), which checks only `kit.assert_inside_scratch(tools)`, never sees them. The other three flows' `unittest` wrappers add no assertion beyond the shared body.

## Decisions

- **D1. The conformance suites leave the shards, the managed job is the gate.** `CI_PLACEMENT["conformance"] = (EXCLUDED, <reason>)`. The reason names the managed job. `EXCLUDED` already means "a local full run keeps it, CI's shards do not", which is exactly the case; no new placement kind, no planner change.
- **D2. How the coverage proof is kept.** `tests-result` no longer proves the conformance suites, and the plan states what proves them instead, in three parts, each pinned by a test except where (a) says otherwise: (a) `workflow-conformance` is one of the required checks (`tests/test_ci_workflows.py` already pins the five names) and its file runs on `pull_request` and on `push` to `main` and holds exactly one job; (b) the excluded conformance ids are exactly the suites the managed file lists (`conformance_suites` against the partition), so no suite can be dropped from both places; (c) the managed job's own coverage check (every acceptance test runs exactly once across its four groups) is the proof for the largest suite and is the Workflow Manager's, never edited here. The residual is stated, not hidden: a failure in a suite now shows as the `workflow-conformance` check and not as a `tests (<i>)` shard, and a `main` run's `validate` result no longer includes the suites (the owner's decision of 2026-11-16 is that the managed job is the gate). The residual is narrower than "they gate the merge" and is stated exactly: a pull request need not be up to date with `main` (`docs/guide/ci-and-releases.md`, the ruleset list), and `CI_MAIN_REUSE` reuses the pull request's results only when the recorded tree equals the squash commit's tree. Today, when `main` moved after the pull request's last run, `validate` on `main` runs the full shards, conformance included, and `build-publish` waits for it. After CP1 such a moved-base squash commit can be released with the conformance suites never having run on its tree: the managed job still runs on the push to `main`, but `build-publish` does not wait for it, so a failure there shows after the release decision. This is accepted as part of the owner's decision (see O2) and is not closed by this item. Part (a)'s required-check half is documented in `docs/guide/ci-and-releases.md`, not tested: `test_the_required_check_contexts_are_unchanged` only asserts that `workflow-conformance:` occurs in the managed file, and the ruleset is a repository setting no test can see.
- **D3. The functional flows run once, as scenarios.** `CI_PLACEMENT["tests.test_functional_lifecycle"] = (EXCLUDED, <reason>)`: the scenario copy stays in the shards (and its mutations stay after the merge), the `unittest` copy is excluded. The scenario is kept because it is the one the catalogue and the negative suite use and it runs in the kit's sandbox, which is the stricter of the two. The leftover check is kept on the path that stays in the shards: `functional_tester_isolation` wraps its `tfl.flow_isolation_holds` call in `tfl.short_folders_made()` and asserts, as `IsolationTest` does, that the run built a sandbox and that no recorded short `TMPDIR` folder still exists, so excluding the `unittest` copy loses no assertion. The "same body" claim is pinned by a test that parses `tests/scenarios/lifecycle.py` and requires that the four scenario functions `functional_review_closes_the_cycle`, `functional_review_stops_for_a_person`, `functional_review_without_a_run` and `functional_tester_isolation` (ids `scenario-functional-*`) together call every `flow_*` function defined in `tests/test_functional_lifecycle.py`, each body by exactly one scenario. The test fails when a body is called by no scenario, when two scenarios call the same body, or when a `flow_*` is added to the module and no scenario calls it, so a flow can never be left in no shard.
- **D4. A local run keeps both.** The excluded entries change CI only. `python3 tools/run_tests.py` with no `--ci-placement` still runs the nine suites and the lifecycle module, and a test pins that (the same shape as `test_the_after_merge_modules_stay_in_a_local_full_run`).
- **D5. Nothing is retimed.** `tools/test_timings.json` keeps its atoms for the excluded modules (a local run reads them, and the staleness ratchet counts only CI-placed atoms); the plan digest changes because the selection does.

## Open decisions

No repository decision-record "Open decision" row is touched (this repository has no `docs/TECHNICAL_DECISIONS.md`). Two owner decisions remain (O1 and O2), because they narrow what the owner approved:

- **O2. The moved-base release residual of D2.** Provisional choice: accepted as part of "the managed job is the gate", stated on the pages (CP3) and not fixed here. Closing it would make `build-publish` wait for the managed job's result on the pushed commit, a change to the release workflow that is its own item. **Owner: say "make the release wait" before CP1 starts and the plan is amended with that dependency; otherwise it is added to `docs/backlog.yaml` as its own item when this one is accepted.**
- **O1. Scope item 7 of the owner's decision of 2026-11-16 (cut the Codex loops and one duplicate test, about 1 minute) is not in this item.** Provisional choice: left out, for two reasons. The "exact duplicate test (20 s)" is not identifiable from the source: a comparison of every test body in `tests/` finds identical bodies only in pairs that run on different fixtures (for example the impl and plan usage-limit tests in `tests/test_job_codex_apply.py`, which run the impl and the plan stage) or in the two sandbox modules (`tests/test_codex_sandbox.py` and `tests/test_codex_worker_sandbox.py`, cheap unit tests), so naming one is a measurement task and not a removal. The loops (`RecoveryTest` 82 s, `OutMemberPlantTest` 52.6 s, `PlanBoundaryTest` 51.9 s and the plant loops) each run one whole job per variant and each variant is a distinct refusal; cutting them is a coverage trade that needs a rule per loop, not "run each test once", and the saving is under a minute of wall time. So this item stays one mechanism (the placement). **Owner: say "include item 7" before CP1 starts and the plan is amended with a fourth checkpoint; otherwise item 7 is added to `docs/backlog.yaml` as its own item when this one is accepted.**

## Checkpoints

<!-- CP1 -->
### CP1: the conformance suites leave the shards

Add the `conformance` entry to `CI_PLACEMENT` in `tools/test_shards.py` (D1) with its reason, and update the comments that describe the placement kinds and the conformance lane to say the lane is local-only under `--ci-placement`. In `tests/test_test_shards.py` and `tests/test_ci_workflows.py` change the assertions that assume the conformance family is in the shards (`test_the_real_inventory_is_partitioned_exactly` and `test_the_placed_modules` and any that the run names) and add the proof of D2: the excluded conformance ids equal the suites read from the managed file; the managed file runs on `pull_request` and on `push` to `main` and has one job; `workflow-conformance` is a required check name; a `--ci-placement` plan holds no `conformance:` id; and a local selection (D4) still holds all nine. Print the plan digest and the three shard estimates before and after in the checkpoint's verification record so CP3 can quote them. The item page's current flow figures (738 s and 737 s) are from the CI2 sizing; the plan's 691 s and 640 s are the committed timings, and CP1's record names which source each figure is from so a reader does not compare them.

**Files:** `tools/test_shards.py`, `tests/test_test_shards.py`, `tests/test_ci_workflows.py`

**Tests:** `tests.test_test_shards`, `tests.test_ci_workflows`, `tests.test_run_tests`, `tests.test_ci_changes`, `python3 tools/ci_workflows.py --check`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: the functional flows run once

Add the `tests.test_functional_lifecycle` entry to `CI_PLACEMENT` (D3) with its reason. In `tests/test_test_shards.py` add the placement assertions and the pin of the "same body" claim (D3): parse `tests/scenarios/lifecycle.py` and require that the four scenario functions named in D3 together call every `flow_*` defined in `tests/test_functional_lifecycle.py`, each by exactly one scenario, with a test that the pin fails when one body is called by no scenario, and that the four `tests.test_scenarios.Scenario_scenario_functional_*` atoms are in the `shards` part, while the four `tests.test_functional_lifecycle` test ids are in `excluded` and still in a local selection (D4). `tests/test_ci_workflows.py`'s `test_the_placed_modules` lists the new module. In `tests/scenarios/lifecycle.py` move the leftover check into `functional_tester_isolation` (D3): wrap the `tfl.flow_isolation_holds` call in `tfl.short_folders_made()` and assert that a sandbox was built and no recorded short `TMPDIR` folder remains, keeping `IsolationTest` as it is for a local run.

**Files:** `tools/test_shards.py`, `tests/test_test_shards.py`, `tests/test_ci_workflows.py`, `tests/scenarios/lifecycle.py`

**Tests:** `tests.test_test_shards`, `tests.test_ci_workflows`, `tests.test_scenarios`, `tests.test_run_scenarios`, `python3 tools/ci_workflows.py --check`.
<!-- /CP2 -->

<!-- CP3 -->
### CP3: the pages, the measured effect and the item record

State the change on the pages that describe the shards and the conformance lane: `docs/guide/ci-and-releases.md` (the shard description and the managed-job paragraph, the "what bounds the longest shard" paragraph that names the `acceptance_matrix` floor, which is gone from the shards, the conformance-lane section, which now says the lane is local-only, and the release paragraph that says what a `main` run's `validate` no longer proves, stating D2's moved-base residual exactly: when `main` moved after the pull request's last run, a squash commit can be released although the conformance suites never ran on its tree, because `build-publish` does not wait for the managed job) and `docs/guide/development.md` (the test-runner section's conformance and placement sentences). Record the measured effect in `docs/items/workflow-controller-ci2b.md` the way the CI2 page does: the base plan (digest `3198ddc7`, three shards each estimated at 3,494.0 s by the planner) against the new plan from `python3 tools/run_tests.py plan --profile ci --ci-placement`, the list of excluded atoms with their committed seconds (2,271 s and 691 s at the base), the elapsed time of this pull request's own run against the CI2 baseline (55 min 41 s), and what remains (the lifecycle and Codex families, [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) still open); give the item page its status and the plan link and keep O1's and O2's outcomes on it.

**Files:** `docs/guide/ci-and-releases.md`, `docs/guide/development.md`, `docs/items/workflow-controller-ci2b.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-ci2b`.
<!-- /CP3 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | The nine conformance suites run once on a pull request (the managed `workflow-conformance` job), not also in the shards, and the proof that keeps them gated is stated and pinned by tests | CP1 |
| R2 | The four functional flows run once on a pull request, as scenarios, and a local full run keeps both copies | CP2 |
| R3 | The pages and the item record state the change, the kept proof and the measured effect, and record the owner's answer on scope item 7 | CP3 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The conformance suites leave the shards | - | 2 | 1 |
| CP2 | The functional flows run once | CP1 | 1 | 1 |
| CP3 | The pages, the measured effect and the item record | CP2 | 1 | 1 |
