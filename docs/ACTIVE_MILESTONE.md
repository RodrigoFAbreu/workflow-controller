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
| CP1 -- inventory, atoms and selection | complete | `302d7fc` |
| CP2 -- result records and timing model | complete | this checkpoint's commit |
| CP3 -- adaptive deterministic planner | not started | |
| CP4 -- executor, local runner and aggregation | not started | |
| CP5 -- serialization registry and timing-flake hardening | not started | |
| CP6 -- CI integration | not started | |
| CP7 -- documentation, measurement and full verification | not started | |

### CP2 -- result records and timing model (complete)

- **`tools/test_shards.py`** gains the timing layer, still stdlib only.
  - **Result records.** `validate_shard_result(record)` checks the exact `shard-<i>.json` shape
    from plan section B, and names every problem in one `ResultRecordError`. The shape is:
    - the top-level keys, exactly;
    - `tests` entries `{id, outcome, seconds, detail?}`, with `outcome` in the six unittest
      outcomes;
    - `fixture_errors` entries `{description, traceback}`;
    - `fixture_skips` entries `{description, reason}`;
    - `atoms` as `{key: seconds}`;
    - `leaked_processes` entries `{pid, argv, age_seconds}`;
    - every duration finite and non-negative, and the timestamps ISO.

    Coverage (duplicate, missing or unplanned ids) is deliberately left to CP4's aggregate, so
    that it can name them. `load_shard_result(path)` reads and validates one file, naming it.
    `record_passes(record)` is the record's own verdict: every outcome in
    `{pass, skip, expected_failure}` and no fixture error; fixture skips never count.
    `holder_target()` parses an `_ErrorHolder` description such as `tearDownModule
    (tests.test_release_txn)` into the module or class it names.
  - **Timing files.** `parse_timings`/`load_timings` read the plan's schema,
    `{schema_version: 1, profile, atoms: {key: {seconds, samples, tests}}, updated_from}`,
    together with the file's SHA-256, which CP3's `timing_source` needs. A file that is
    missing, unreadable, not UTF-8 or JSON, schema-invalid, or has a negative or non-finite
    duration yields `DEFAULT_TIMINGS`, with exactly one warning line naming the file (on stderr
    by default). It never raises. `write_timings` writes canonical sorted JSON to an adjacent
    temporary file, fsyncs it, then `os.replace`s it. A failed write leaves the old file and no
    temporary behind. `CI_TIMINGS` is `tools/test_timings.json`, which CP4 seeds.
    `local_timings_path()` is `$XDG_CACHE_HOME/workflow-controller-tests/timings-local.json`,
    or `~/.cache/...` if that variable is unset or empty.
  - **Update policy.** `update_timings(profile, records, inventory, profile_name=None)` does the
    following:
    - folds the records, oldest first, with `seconds = 0.5·observed + 0.5·old` and `samples + 1`;
    - makes an atom that is new, or whose inventory test count differs from the profile's, take
      the observation outright;
    - prunes every profile atom the inventory no longer contains.

    An atom is merged only if each of its inventory tests has exactly one passing entry in the
    record, and no fixture error or fixture skip touches it (same key, a module fixture over a
    class, or a class fixture inside a module atom). So a failed, errored, interrupted or
    fixture-skipped atom is not merged. A record with an unparseable holder description merges
    nothing.
  - **Estimates.** `estimate_atoms(atoms, profiles)` returns exactly one estimate per given
    atom, and nothing else (I3). The profiles are consulted in priority order: local, then the
    committed CI profile.
    - A known atom uses the first profile that records it.
    - An unknown conformance suite gets 30 s.
    - An unknown controller atom gets `tests × mean seconds per test`. The mean comes from its
      module in the first profile that has that module, else from the first non-empty profile
      (conformance entries excluded), else the default of 0.25 s.
- **Two interpretations the plan leaves open, and why:**
  - **A partially selected atom is not merged.** Its duration covers only a subset, the same
    "not representative" reason the plan gives for failed atoms. The "every test has one passing
    entry" rule covers it, together with interrupted and duplicated ids.
  - **A known atom whose selected test count differs from the recorded count is scaled per
    test.** Without scaling, selecting one test from each of several large classes would plan
    many shards for seconds of work. When the counts match (every full run), the estimate is
    exactly the recorded seconds.
  - Separately, `updated_from` is kept ordered, unique and capped at the 20 most recent plan
    digests, so that the local profile does not grow on every run.
- **`tests/test_test_shards.py`** grows to 60 tests. The 30 new tests, in `ResultRecordTest`,
  `TimingFileTest`, `TimingUpdateTest`, `EstimateTest` and `RealInventoryTimingTest`, cover:
  - the record schema, each malformation named, unreadable records named, the verdict per
    outcome, and holder parsing;
  - every fallback case with one warning, the stderr default, the atomic write (and a failed
    one), and the local path;
  - EWMA, test-count reset, pruning, every non-merge case (failed, errored, unexpected success,
    interrupted, class and module fixture skips, a fixture error, a duplicated id, a partial
    selection, an unattributable holder), and the bounded `updated_from`;
  - estimates for known, partial, unknown-in-known-module, unknown-module and
    unknown-conformance atoms, and profile priority;
  - the defaults and a corrupt file each give every atom of the real full and CI selections a
    finite positive estimate, with the selection unchanged.

  The plan's "and a valid plan" clauses need CP3's planner. CP3's property tests plan from
  these same default and corrupt-file inputs.
- Mutation check: changing the EWMA weight, accepting booleans as numbers, dropping the fixture
  overlap check or its class-inside-module direction, or letting conformance entries into the
  per-test mean each fails the new tests.
- Verification:
  - `python3 -m unittest tests.test_test_shards tests.test_ci_workflows` passes (109 tests).
  - The full serial suite (`python3 -m unittest discover -s tests -t .`), run under a reaping
    subreaper wrapper with SIGINT at `SIG_DFL` (this session is a Controller worker), ran 1947
    tests in 518 s: OK, 8 skipped.
  - The real inventory is now 423 atoms, because the new test classes are in it.

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
