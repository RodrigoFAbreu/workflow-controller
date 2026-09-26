# Active Milestone

## Milestone

`workflow-controller-adaptive-test-sharding`: one test inventory, duration-balanced shards, local
and CI. The operator requested it directly, ahead of `docs/ROADMAP.md` section 1.4, which stays
the next roadmap item.

- Plan: `docs/ai-workflow/CONTROLLER_ADAPTIVE_TEST_SHARDING_PLAN.md`, revision 3, approved at
  `5fdea5a` (`EXTERNAL_APPROVE`).
- Registry: `docs/ai-workflow/registry/workflow-controller-adaptive-test-sharding-registry.json`
  (CP1-CP7).
- Governing workflow version: `2.2`. Base commit: `405f050`.
- Ground truth for phase and checkpoint status: `docs/ai-workflow/WORKFLOW_STATE.json`.

The previous milestone, `workflow-controller-worker-lifecycle-ownership`, is complete. Its
narrative is archived at
`docs/milestones/completed/workflow-controller-worker-lifecycle-ownership.md`.

## Goal

Cut the wall-clock time of the Controller's full verification by running the same test selection
in duration-balanced parallel shards. One deterministic inventory and planner is shared by local
runs and CI. Coverage is unchanged and proven at run time. There are no test tiers, and there are
no retries.

## Checkpoint progress

| id | status | commit |
| --- | --- | --- |
| CP1 -- inventory, atoms and selection | complete | this checkpoint's commit |
| CP2 -- result records and timing model | not started | |
| CP3 -- adaptive deterministic planner | not started | |
| CP4 -- executor, local runner and aggregation | not started | |
| CP5 -- serialization registry and timing-flake hardening | not started | |
| CP6 -- CI integration | not started | |
| CP7 -- documentation, measurement and full verification | not started | |

### CP1 -- inventory, atoms and selection (complete)

- **`tools/test_shards.py`** (new, stdlib only, not imported by `controller/`).
  - `build_inventory(repo_root)` returns one `Inventory`. It holds the `controller` family,
    exactly what `unittest discover -s tests -t <repo>` loads, in discover's order. The
    `conformance` family follows it: one `conformance:<file>` id per `run: python3 <file>` line of
    the managed `workflow-conformance.yml`, in that file's order.
  - Each of these refuses the whole inventory with `InventoryError`, naming every cause:
    - any loader error, and any `_FailedTest`, `ModuleImportFailure`, `_ErrorHolder` or
      `ModuleSkipped` in the loaded suite;
    - a duplicate id;
    - a test not loaded from a module as a `TestCase`;
    - a class not bound in its loading module under its own name;
    - an atom whose ids are not contiguous in discover's order;
    - a managed workflow with no suite, a duplicate suite, or a suite that is not a file in
      `scripts/`.
  - An atom is one of three things:
    - a class, keyed `<loading module>.<ClassName>`. A `TestLoader` subclass tags each module's
      suite with its module, so the key is the loading module even for an imported class;
    - the whole module, when the loaded module object defines `setUpModule` or `tearDownModule`.
      Today that is `tests.test_release_txn` and `tests.test_forge`;
    - a conformance suite.
  - `Inventory.select(names, ci_placement=False)` gives set semantics over canonical ids:
    - no names selects everything;
    - a dotted name matches by whole-component prefix, so `tests.test_work` does not match
      `tests.test_worker`;
    - `conformance` selects every suite, and `conformance:<file>` selects one;
    - any unmatched name refuses with `SelectionError`, naming each such name;
    - a partial atom keeps its key and holds the selected subset.
  - `CI_PLACEMENT` puts `tests.test_packaged_runtime` in `package` and
    `tests.test_integration_disposable_repo` in `excluded`, each with a reason.
    `ci_partition(inventory)` splits the ids into `shards`, `package` and `excluded`. A placed
    module missing from the inventory refuses.
- **`tests/test_test_shards.py`** (new, 30 tests).
  - Synthetic trees, each discovered from a throwaway package and then dropped from
    `sys.modules` and `sys.path`, cover:
    - a clean tree equals discover;
    - an import error, a `_FailedTest` with no loader error, an `_ErrorHolder`, a module that
      raises `SkipTest` at import, and a duplicate id each refuse;
    - `setUpModule` and `tearDownModule` each form a module atom;
    - a class is keyed by its loading module;
    - a renamed binding, and an interleaved atom, each refuse.
  - The conformance family: the real family equals the managed `run:` lines, order is kept, and
    each refusal fires.
  - The real inventory:
    - its controller ids equal discover's ids, in discover's order;
    - the conformance ids follow the controller ids;
    - the atoms partition the ids in canonical order;
    - only the two fixture modules are module atoms;
    - each class atom's name selects exactly its own ids.
  - Selection by module, class, method, `tests`, `conformance` and `conformance:<file>`: names
    union in canonical order, and a prefix must match whole components. Unmatched names refuse,
    and a partial module atom holds the selected subset.
  - The real inventory's CI partition is exact, and the CI selection equals its `shards` part.
- **Interim CI placement.** `tools/ci_workflows.py`'s hand-curated `CONTROLLER_SHARDS` gains
  `test_test_shards` in its `docs` shard, and `.github/workflows/validate.yml` is re-rendered.
  This keeps `CoverageTest.test_every_test_module_is_placed_exactly_once` passing, and runs the
  new module in CI until CP6 replaces the hand-curated matrix.
- Measured: the real inventory holds 1894 ids (1887 controller + 7 conformance) in 418 atoms.
  The CI partition is 1865 `shards`, 9 `package` and 20 `excluded`. Discovery takes about 0.1 s
  in process.
- Verification: `python3 -m unittest tests.test_test_shards tests.test_ci_workflows` passes (79
  tests). `tools/ci_workflows.py --check` is clean.
- Full serial suite (`python3 -m unittest discover -s tests -t .`): 1917 tests in 648 s. 1912
  passed, 8 were skipped, and 5 failed for an environmental reason. The machine was loaded by
  unrelated processes.
  - The 5 failures are `tests.test_lock.InheritedDescriptorTest` and `tests.test_resume`'s
    `OrphanWorkerTest`, `UnreconcilableOrphanTest`, `EndToEndInterruptionTest` and
    `BootstrapEndToEndInterruptionTest`. Each asserts that an orphaned worker group is gone.
  - This session ran as a Controller-launched worker. The Controller is a child subreaper, so the
    orphans reparent to it and linger as zombies, and `killpg(pgid, 0)` still succeeds.
  - Re-run under a wrapper that is itself a reaping subreaper, as systemd is outside the
    Controller, the 6 tests pass in 3 s. CP1 touches none of the code they exercise.
