# CI2f: process starts per lifecycle step (Revision 2)

Work item: `workflow-controller-ci2f`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `55ad7ffda7e8be1dba378ac97bb53b82ba750157` ("ci: prove the documentation set at run time, not from source text (CI2e) (#379)"), the tip of `main`, passed explicitly as `/milestone-plan 55ad7ffda7e8be1dba378ac97bb53b82ba750157`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ci2f` (`docs/backlog.yaml` order 100, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ci2f.md`. It depends on `workflow-controller-ci2`, which is accepted.
Released baseline: the latest tag. The change edits tests, test helpers and pages and no `controller/` module, so the squash commit is a `ci:` and publishes no release.
Pull request title: `ci: start fewer processes per lifecycle step in the tests (CI2f)`

## Goal

One lifecycle step in the tests starts many processes. Counted with a `subprocess.Popen` hook over `tests.test_protocol_lifecycle.ProtocolModeLifecycleTest` (ten steps, 8.8 s), the test process starts 554 processes: 459 `git`, 64 protocol interpreters (`workflow_protocol.py`: 25 `describe`, 22 `next-action`, 11 `verify`, 6 `reconcile`), 20 stub `workflow-manager` runs and a few setup commands. Every protocol query is one interpreter plus six `git` calls of the query's isolation (`_git_isolation`: two `rev-parse`, three `config`, one `ls-files`), about 0.13 s each. The lifecycle family is 45 percent of the Controller test time ([CI2](../items/workflow-controller-ci2.md), "Measured findings"), so a query the tests ask for and need not ask for is paid thousands of times a run ([#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317)).

One mechanism: **the lifecycle drivers in the tests ask for fewer things per step.** Production code is not touched. Two of the owner's scope items (8 and 9 of the decision of 2026-11-16 on the CI2 page) are decided here: item 9 is done; item 8, which touches the production isolation path, is measured and not done (D4).

## What exists

- **The per-step queries.** `job.execute_step` asks the Workflow for one `describe` at admission (`managed_repo.inspect`, `_check_protocol_admission`), one `verify` (`protocol_decision.preflight`), one `next-action` to decide (`protocol_decision.decide`), one more `next-action` bound to the state identity (`check_currency`), and, once a worker has run, a `describe` or two to compare the release (`job._protocol_identity_change`, `_protocol_release_change`) and one `reconcile`. Each is a safety check of the production path and stays.
- **The extra queries the tests add.** Six sites record the row the Workflow answered before a step with a direct `protocol.next_action(...)` call: `GateLifecycle.step` and `LoopLifecycle._step_with_budget` (`tests/test_protocol_lifecycle.py`), `TesterLifecycle.step` (`tests/test_protocol_job.py`), `RealLaunch.step` (`tests/test_functional_lifecycle.py`, through `life.row()`), `CodexLifecycle.step` (`tests/test_functional_codex_route.py`) and one inline site, `TheFaultStopsSandboxTest` (`tests/test_protocol_job.py`, around a direct `execute_step` call). `FunctionalCycle` has no `step` of its own (it inherits `TesterLifecycle.step`), and its `row()` calls after a step are assertions on the state afterwards, not queries before one: they stay. The step then calls `execute_step`, whose `decide` asks the same question again. Four drivers (the ones in `tests/test_protocol_lifecycle.py`, `tests/test_protocol_job.py`, `tests/test_protocol_outcome.py` and `tests/test_review_round_lifecycle.py`) define `inspect()` as a plain `managed_repo.inspect(self.root, manager_bin=str(self.stub))`, so every step pays one `describe`, two stub runs and about seven `git` calls to learn what the previous step already learned.
- **The vendored interpreters.** `controller/workflow_contract.py` starts each query as `python -B -E -s <private copy>` (`_copy_and_execute`). `-B` keeps bytecode out of the private directory. The scope item asks whether reusing bytecode is worth the production risk.
- **The stub Manager.** `fixtures.write_stub_workflow_manager` writes a static shell script: its `verify` and `status` answers are a function of its own bytes.

## Decisions

- **D1. The row is read from the step's own decision, not asked for again.** A context manager in `tests/test_protocol_lifecycle.py` (`Lifecycle.decided_row`) wraps `protocol.next_action` for the duration of one `execute_step` call and records the `raw["row"]` of the first call made **without** `expect_state_identity` (the `decide` call; the `check_currency` call carries the identity). The wrapper passes every call through unchanged, so the production sequence of queries is unchanged. The six sites append that row after the step instead of asking before it, **in a `finally`**, so a step that raises still records its row exactly as today (two `TesterLifecycle` tests run `life.step()` under `assertRaises`); `DecidedRowTest` pins that a raising step still appends the row it decided. When the step made no such call (a refusal before the decision), the row is asked for directly after the step; the state of such a step is unchanged by construction, and a test pins that case. Why not read it from the job record: a gate-blocked step writes no job record with a protocol block the tests can read in every case, and the spy needs no production change.
- **D2. `inspect()` is cached in the lifecycle drivers, keyed on the bytes it reads.** `tests/fixtures.py` gets `inspect_unchanged(root, manager_bin)`: it returns a previous successful `managed_repo.inspect` result when the key is equal, otherwise it runs `managed_repo.inspect` and keeps the result. The key is the root path, the manager stub's path and bytes, the bytes of `.workflow-manager/installation.json` and the sha256 of every file that record lists as managed and the stub's mode bits (`_resolve_manager_bin` tests the executable bit). What `inspect` reads is the record, the listed scripts and the stub's two answers. Assumptions the key does not cover, stated: the target's Git configuration and hook files (which `_git_isolation` refuses on before `describe` runs) do not change between two steps of one test, and no reader mutates the result's mutable members (`manifest`, `verify`, `status` and `script_digests` are dicts inside the frozen dataclass; no current reader does, and `InspectCacheTest` returns a deep copy to keep it so). A refusal is never cached, the result is a frozen dataclass so sharing it is safe, and only the latest entry per root is kept. The four drivers' `inspect()` methods call it; tests that exercise `managed_repo.inspect` itself (admission, drift, resume) keep calling it directly and are not touched. Production `inspect` is not cached anywhere: each real step must still check the installation.
- **D3. The saving is counted, not assumed.** `fixtures.count_process_starts()` is a context manager that hooks `subprocess.Popen.__init__` and returns a `Counter` by kind (`git`, `workflow_protocol` by operation, the stub manager, other). It is the instrument of the measurements, and tests use it to prove each change is a **relative** saving (the same step with and without the old call differs by exactly the removed query), not an absolute budget that a later production change would break.
- **D4. Scope item 8 (bytecode for the vendored interpreters) is measured and not done.** One interpreter's compile cost is small: compiling all of the 2.7.0 scripts (25,762 lines) takes 65 ms in one process, and a query run with `-X pycache_prefix` warm against the same query with `-B -E -s`, best of seven on a seeded 2.12.0 repository, took 114 against 122 ms (`describe`), 133 against 139 ms (`next-action`) and 120 against 127 ms (`verify`): about 5 percent of a query, about 50 ms of a step, which is below the change's own risk (a cache directory the isolation path would have to trust, `controller/workflow_contract.py` `-B`, `interpreter_environment`). The measurement and its commands go on the item page. It is a local measurement; the owner's rule names a runner, see O1.
- **D5. What is not proven, stated.** Production queries per step are unchanged, so the CI time saved is the test drivers' share only (the extra `next-action` and the repeated `describe` per driven step). The gain is measured locally on the lifecycle modules and reported with the local-versus-runner caveat CI2c used.

## Open decisions

This repository has no `docs/TECHNICAL_DECISIONS.md`; no decision-record "Open decision" row is touched. One owner decision, provisional:

- **O1. Item 8 on a runner.** D4 closes the bytecode idea on a local measurement (about 5 percent of a query). The owner's wording is "one `-X importtime` measurement on a runner decides". **Owner: say so and a follow-up item measures it on a runner and revisits; otherwise it stays closed.**

## Checkpoints

<!-- CP1 -->
### CP1: count the process starts

Implement D3 in `tests/fixtures.py`: `count_process_starts()` (a context manager over `subprocess.Popen.__init__`, restoring it on exit and on error, classifying each start as `git`, `protocol:<operation>`, `manager` or `other`). Add `tests/test_step_cost.py` with `CounterTest`: it counts a known `git` call, a known interpreter run and a stub manager run, restores `Popen` afterwards, nests, and a start inside a thread is counted. Record the baseline per-step counts of the five drivers in the item page text of CP4 (kept in the checkpoint's verification output until then).

**Files:** `tests/fixtures.py`, `tests/test_step_cost.py`

**Tests:** `tests.test_step_cost`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: read the row from the decision

Implement D1: `Lifecycle.decided_row` in `tests/test_protocol_lifecycle.py` and its use at the six sites (`GateLifecycle.step`, `LoopLifecycle._step_with_budget`, `TesterLifecycle.step` and the inline pre-step query of `TheFaultStopsSandboxTest` in `tests/test_protocol_job.py`, `RealLaunch.step` in `tests/test_functional_lifecycle.py`, `CodexLifecycle.step` in `tests/test_functional_codex_route.py`). The post-step `row()` assertions of `FunctionalCycle` stay. `tests/test_step_cost.py` adds `DecidedRowTest`: on two lives built from the same snapshot the spy row equals the direct row; a step with the spy starts exactly one `next-action` query fewer than a step that also asks directly; the identity-bound call is not mistaken for the decision; a step that never decides falls back to a direct call; a step that raises still appends its decided row; every call passes through unchanged. While running the lifecycle modules once, the self-review also asserts (not committed) that the spy row equals the direct row at every step.

**Files:** `tests/test_protocol_lifecycle.py`, `tests/test_protocol_job.py`, `tests/test_functional_lifecycle.py`, `tests/test_functional_codex_route.py`, `tests/test_step_cost.py`

**Tests:** `tests.test_step_cost`, `tests.test_protocol_lifecycle`, `tests.test_protocol_job`, `tests.test_functional_lifecycle`, `tests.test_functional_codex_route`, `tests.test_pr_report` (asserts the recorded row `38f`), `tests.test_shared_snapshot`, `tests.test_functional_select`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP2 -->

<!-- CP3 -->
### CP3: cache `inspect()` in the lifecycle drivers

Implement D2: `inspect_unchanged` in `tests/fixtures.py` and its use in the four drivers' `inspect()` (`tests/test_protocol_lifecycle.py`, `tests/test_protocol_job.py`, `tests/test_protocol_outcome.py`, `tests/test_review_round_lifecycle.py`). `tests/test_step_cost.py` adds `InspectCacheTest`: a second call starts no process and returns an equal result to a fresh `managed_repo.inspect`; the key moves when the record, a managed script or the stub changes (each, in turn) and the answer then equals a fresh inspect; a refusal (a drifted stub) is raised again on the next call and never cached; two roots do not share an entry; the stub's mode bits move the key; the cached result is a copy, so mutating it cannot reach the next call; `ReleaseMovedBetweenStepsTest` still refuses the stale step. `tests/scenarios/review.py` builds a `Lifecycle` and so reaches the cached `inspect()`; it runs through `tests.test_run_scenarios`, which the CP3 selection names.

**Files:** `tests/fixtures.py`, `tests/test_protocol_lifecycle.py`, `tests/test_protocol_job.py`, `tests/test_protocol_outcome.py`, `tests/test_review_round_lifecycle.py`, `tests/test_step_cost.py`

**Tests:** `tests.test_step_cost`, `tests.test_protocol_lifecycle`, `tests.test_protocol_job`, `tests.test_protocol_outcome`, `tests.test_review_round_lifecycle`, `tests.test_shared_snapshot` (compares `inspect().target_protocol` across restored snapshots that reuse a root path), `tests.test_pr_report`, `tests.test_functional_select`, `tests.test_run_scenarios`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP3 -->

<!-- CP4 -->
### CP4: the pages and the item records

State the mechanism on `docs/guide/development.md` (next to "Shared lifecycle snapshots": the per-step queries the production path keeps, what the drivers no longer ask, `inspect_unchanged` and its key, `count_process_starts`), and record the item on `docs/items/workflow-controller-ci2f.md` (status, the plan link, the baseline and after process counts per step, the local wall time of the lifecycle modules before and after with the local-versus-runner caveat, the D4 bytecode measurement with its commands, and O1) and `docs/items/workflow-controller-ci2.md` (the slice marked implemented). `docs/backlog.yaml` and `docs/ROADMAP.md` are unchanged: the item stays `next` until acceptance.

**Files:** `docs/guide/development.md`, `docs/items/workflow-controller-ci2f.md`, `docs/items/workflow-controller-ci2.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-ci2f`.
<!-- /CP4 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | A test can count the processes a step starts, and the counter restores what it hooks | CP1 |
| R2 | A lifecycle driver step no longer asks the Workflow for the row it is about to decide, with the production queries unchanged and the saving proven as one query | CP2 |
| R3 | The lifecycle drivers do not repeat an `inspect` whose inputs are unchanged, and a changed or refused inspect is never served from the cache | CP3 |
| R4 | The pages and item records state the mechanism, the measured counts, the bytecode decision and the open owner question | CP4 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Count the process starts | - | 1 | 1 |
| CP2 | Read the row from the decision | CP1 | 2 | 1 |
| CP3 | Cache inspect in the lifecycle drivers | CP2 | 2 | 1 |
| CP4 | The pages and the item records | CP3 | 1 | 1 |
