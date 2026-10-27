# H3: the worker launch behind the harness adapter (Revision 1)

Work item: `workflow-controller-worker-launch`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `cdd300d851062cdd16bd51ce396b9aad4a5d8c3c` ("docs: reword the SC1 roadmap row, link the H3 issue and point the next action at H3 (#192)"), the tip of `main`, passed explicitly as `/milestone-plan cdd300d…`.
Lifecycle authority: this repository's installed Workflow **2.22.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H3, next after SC1 (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#191](https://github.com/RodrigoFAbreu/workflow-controller/issues/191).
Released baseline: `workflow-controller 1.22.2`. A pure refactor with no change a user can see, so the squash commit is a `chore:` change and releases nothing (D6).
Pull request title: `chore: put the worker launch behind the harness adapter (H3, #191)`

## Goal

A Codex worker (H4a to H4g) needs a place to plug in. Today the lifecycle worker is launched by one hard-wired call, `worker.launch(...)` in `controller/job.py` (`_launch_job`, reached from `execute_step`), whatever harness the worker's route names. H1 made the route name a harness and refuses an adapter that does not declare the `worker` role, but nothing turns that harness into a launch: A1 registered the declarations and left the launch code where it was.

This milestone adds the missing seam and nothing else. One concern only: **the lifecycle worker is launched through the launcher of the routed worker harness, and Claude Code's launcher is the code that runs today.** No setting, no harness, no argv, environment, supervised process, gate, record field or exit code changes.

## Decisions

Each decision states one principle.

- **D1. The launch interface is a small module next to the adapter, not a field on it.** `controller/harness.py` stays plain declarations that import only `controller.errors` (pinned by `tests.test_harness.ImportSetTest.test_imports_only_errors`), and `controller/worker.py` imports no adapter. A new module `controller/worker_launch.py` holds the interface and the registry: a `WorkerLauncher` protocol with one method, `launch(task, **options)`, returning what `worker.launch` returns; `ClaudeCodeLauncher`, the one implementation; `LAUNCHERS`, a mapping keyed by adapter name; and `for_harness(name)`.
- **D2. The Claude Code launcher is `worker.launch`, looked up when it is called.** `ClaudeCodeLauncher.launch(task, **options)` is exactly `return worker.launch(task, **options)`, where `worker` is the module and `launch` is read as an attribute on every call, never captured at import or at construction. The roughly 88 test references that patch `worker.launch` by name (`mock.patch.object(worker, "launch", ...)`) therefore keep working untouched, and a test pins it (CP1). The options pass through unchanged, so the arguments that reach `worker.launch` are the ones `job.py` passes today, and `controller/worker.py` is not edited.
- **D3. The routed harness picks the launcher, and the registry cannot drift from the declarations.** `for_harness(name)` calls `harness.for_role(name, harness.WORKER)` (H1's refusal of an adapter without the `worker` role, a `HarnessRoleError`) and returns the launcher registered under the adapter's `name`, so `claude-code` and its reviewer name resolve alike. `_execute_step_locked` resolves it with `route.harness` right after the worker's route is resolved and before usage admission and before anything is recorded, and passes the resolved launcher to `_launch_job` (where the launch happens), so an unlaunchable harness would leave no record behind, as an unroutable action does. With today's registry, the only harness H1 accepts for a worker role is `claude-code`, so the lookup never refuses and every run launches as before. A test requires that the adapters declaring the `worker` role are exactly the keys of `LAUNCHERS`; an adapter registered with the role and no launcher fails the suite, not a run (`for_harness` raises `HarnessRoleError` for that case too, which the test makes unreachable).
- **D4. Nothing is generated, because nothing new is declared.** "Can take the worker role" already is the `Roles` column of the generated harness table (H6), and the launcher registry is pinned equal to it (D3), so no column is added and `tools/harness_table.py --check` passes unchanged.
- **D5. Scope is the lifecycle worker launch.** The review launch (`review.launch_review`, ADR 0013), the functional tester's launch (`controller/functional_review.py`) and `claude_build.py` are not touched, nor are supervision, the anchor, the drain or the subreaper in `controller/worker.py`. Codex declares no worker launcher, and H4a to H4g, H9 and any new setting stay out.
- **D6. Nothing a user sees changes, so nothing is released.** `controller/` changes, but no behaviour does; the repository's release policy maps `refactor:` to a patch release and `chore:` to none, so the pull request is titled `chore:`. The existing tests stay unchanged: the only new test file is `tests/test_worker_launch.py`, and the only edit to an existing test is one pure registration, the `worker_launch` entry in `DEPENDENCY_ORDER` of `tests/test_package_structure.py` (no assertion changes), which the package-structure tests require for any new `controller/` module (`controller/__init__.py` imports every module eagerly, its `__all__` must equal the set of `controller/*.py` stems, and `test_init_order_matches_the_dependency_order` compares it with `DEPENDENCY_ORDER`). Any other edit to an existing test would have to be justified here as a pure rename (there is none).

## Out of scope

A Codex worker or any second launcher (H4a to H4g); fallback chains (H9); the review and tester launches; any change to `controller/worker.py`, to the supervision or the drain; a new setting, routing key, record field, event or exit code; an edit to an existing test beyond the one `DEPENDENCY_ORDER` registration D6 names; a new subprocess start (so `tests/process_fixtures.py` is not edited).

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The launcher interface, the Claude Code launcher and the call site | - | 2 | 1 |
| CP2 | The guides and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The launcher interface, the Claude Code launcher and the call site

**Files:** `controller/worker_launch.py`, `controller/__init__.py`, `controller/job.py`, `tests/test_worker_launch.py`, `tests/test_package_structure.py`

D1, D2, D3, D5. `controller/worker_launch.py` as D1 describes. In `controller/job.py` the import and four edits across two functions: in `_execute_step_locked` (`controller/job.py:8257`), the launcher resolved from `route.harness` next to `route = _worker_route(...)`, before the usage-admission gate and before any record exists, and forwarded as a new `launcher` keyword on the `_launch_job(...)` call; in `_launch_job` (`controller/job.py:8444`), the `launcher` parameter added to its signature, and the call at `worker.launch(` replaced by `launcher.launch(` with the same positional task and the same keywords in the same order. No other line of `job.py` changes, and the arguments the underlying `worker.launch` receives are unchanged. `controller/__init__.py` registers the module in its two literals (the eager `from . import (...)` and `__all__`), and `tests/test_package_structure.py` gets one `worker_launch` entry in `DEPENDENCY_ORDER`, after `worker`, `harness` and `handoff` and before `job` (a pure registration, D6).

Tests, all in the new `tests/test_worker_launch.py`, none needing a sandbox or a real `claude`:
- call-time lookup: a `ClaudeCodeLauncher` built first, then `worker.launch` patched, then `launch` called; the patched function receives the call, and the result is returned as is (so patching after construction, or per test, still works);
- equivalence: with `worker.launch` patched, the positional task and every keyword reach it unchanged for a representative call carrying every one of the sixteen keywords the `worker.launch(` call in `job.py` passes (`cwd`, `permission_mode`, `timeout`, `stdout_path`, `stderr_path`, `claude_bin`, `pass_fds`, `on_spawn`, `on_group_drain`, `model`, `effort`, `disallowed_tools`, `ownership_tag`, `supervisor_lock_path`, `on_state_change`, `drain_detach_seconds`); and a pin that `job.py`'s call passes keywords `inspect.signature(worker.launch)` accepts, so a renamed option cannot slip past the pass-through;
- the registry: the adapters declaring `harness.WORKER` are exactly the keys of `LAUNCHERS`; `for_harness("claude-code")` and `for_harness("claude")` return the same launcher; `for_harness("codex")` raises `HarnessRoleError` naming the role (Codex declares no worker role), and a registry whose adapter declares the role without a launcher raises `HarnessRoleError` (built with a patched `ADAPTERS`/`LAUNCHERS`);
- the seam is used: the one `worker.launch(` call site in `controller/job.py` is gone and `launcher.launch(` is the only launch call in `_launch_job`, and `_execute_step_locked` resolves the launcher before the usage gate and forwards it to `_launch_job` (an AST scan of those two functions in the style of the existing structural tests, so the launcher cannot be left undefined at the launch), and an `execute_step` run through the existing fake-claude fixtures with `worker.launch` patched still reaches the patch with the same arguments as before (the existing, unchanged `tests.test_job.CtrlCTest.test_the_interrupt_names_the_worker_and_propagates`, which patches `job.worker.launch` and drives `execute_step` through `_execute_step_locked` to `_launch_job`, is in the run list below and verifies the complete path);
- import discipline, by an AST scan of the source files with no subprocess (so no `tests/process_fixtures.py` inventory row is needed): `controller/worker_launch.py` imports only `controller.harness`, `controller.worker` and `controller.errors` from the package, and `controller/harness.py` and `controller/worker.py` do not import it (the existing `ImportSetTest` stays green).

The run list: `tests.test_worker_launch`, `tests.test_harness`, `tests.test_harness_table`, `tests.test_package_structure`, `tests.test_fake_claude_contract` (its leftover inventory must stay complete: the new test starts no process), the job test modules that patch `worker.launch` (`tests.test_job` including the `CtrlCTest` method named above, `tests.test_protocol_job`, `tests.test_lifecycle_orchestration`), and `python3 tools/harness_table.py --check`, all green and unedited apart from the new file. The full suite is the operator's rerun; two Git 2.56 trailer tests fail locally and are known.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guides and the roadmap

**Files:** `docs/guide/development.md`, `docs/guide/harnesses.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D3, D4, D6. `docs/guide/development.md` ("Adding a harness or a git host"): a harness that takes the worker role also registers a launcher in `controller/worker_launch.py`, and a test fails if the two disagree. `docs/guide/harnesses.md`: one sentence in the prose outside the generated region saying that a worker role is launched through the routed harness's launcher, of which Claude Code's is the only one today; the generated tables are not edited. `docs/ROADMAP.md` row H3 marked complete and the "Next, in order" prose at item 2 ("H3 (...) is next") updated to name the item after it; `docs/ACTIVE_MILESTONE.md` points at the next item. `python3 tools/check_docs.py` passes.
<!-- /CP2 -->

## Functional review notes

Every flow is unit level and runs in the tester's sandbox with no `claude`: the call-time-lookup, equivalence and registry tests of `tests.test_worker_launch`; the harness table check (`python3 tools/harness_table.py --check`); and one routed job test of `tests.test_job` that patches `worker.launch`. The full suite is not a flow.
