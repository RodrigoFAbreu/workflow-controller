# Controller adaptive test sharding: one inventory, duration-balanced shards, local and CI (Revision 7)

Work item: `workflow-controller-adaptive-test-sharding`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `405f050029f546055a3b42783fec25c87873bbd3` ("Add the self-update command to the
roadmap (7.7)"), the tip of `main` when this plan was written. The previous milestone was accepted
at `0ea006c`. The three commits after it are the 1.2.0 version bump (`eefa80b`), the CI-flake fix
the 1.2.0 release needed (`6df5e96`, from which the released `v1.2.0` wheel was built), and a
roadmap edit. None of them is this milestone's work, so the base is the tip, not `0ea006c`.
Lifecycle authority: installed Workflow 2.5.1 (`.claude/commands/`, `scripts/workflow_state.py`,
`scripts/workflow_fingerprint.py`, `scripts/prepare-ai-review.sh`) and each work item's own
`governing_workflow_version`.
Roadmap slot: none yet. The operator requested this milestone directly, ahead of `docs/ROADMAP.md`
section 1.4 ("Follow-up patches"), which stays the next roadmap item and is unaffected. CP7 adds
the roadmap entry.
Released baseline preserved: `workflow-controller 1.2.0` (`v1.2.0`, built from `6df5e9633c56`).
Amendment 0's Design H and amendment 1's drain bound (Design I) are the two Controller changes
for the next release. The released artefact is unaffected.

**Amendment.** Revision 4 is plan amendment `0` (`amendment_history[0]`). It was requested at
`b8b9a0f` from `IMPLEMENTING`, with amendment base `a5fe3f8`, and supersedes revision 3's approval
(`5fdea5a`). It resolves the manual external implementation review's I1 (IR1-003) by fixing a
pre-existing Controller defect that CP5's stress testing exposed. The only scope widening is one
correction in `controller/worker.py`: Design H, delivered by the new checkpoint CP5B. Everything
else in revision 3 stands. "Revision 4" at the end lists what changed and which checkpoints must
be revalidated.

**Amendment 1.** Revision 5 is plan amendment `1` (`amendment_history[1]`), requested by the
operator at `9946567` from `IMPLEMENTING`, with amendment base `ae74df7` (CP5B's commit). It
supersedes revision 4's approval (`8b0f522`). It adds one bounded operational fix that the
operator reproduced in `workflow-manager`: `worker.DRAIN_DETACH_SECONDS` rises from 600 to 10800
(3 hours), delivered by the new checkpoint CP5C (Design I). Nothing else in revision 4 changes.
"Revision 5" at the end lists what changed and how the checkpoints reconcile. Revision 6 answers
the local-model review of revision 5 (round 5, `REVISE`): it corrects what bounds a `resume`
re-attach drain and states that cost, still inside amendment 1 ("Revision 6" at the end).

**Amendment 2.** Revision 7 is plan amendment `2` (`amendment_history[2]`), requested by the
operator at `68ffbbb` from `IMPLEMENTING`, with amendment base `f358409`. It supersedes revision
6's approval (`4a4d0bc`). It resolves the local implementation review's LIR4-001 (round 4). The
functional review's F1 (`9501fbb`) replaced the committed timing seed with CI measurements, and
those CI estimates then set the *local* planner's largest-atom floor: a cold local timing cache
planned 5 shards and took 145.9 s. Revision 7 takes the operator's remedy (c): for local
planning, an estimate taken from the committed CI profile still counts in the total and in the
assignment, but only estimates from the local-machine profile can set the largest-atom floor.
CI planning is unchanged. It is delivered by the new checkpoint CP6B (Design J). Nothing else in
revision 6 changes. "Revision 7" at the end lists what changed and how the checkpoints reconcile.

## Goal

Cut the wall-clock time of the Controller's full verification substantially by running the
**same** test selection in duration-balanced parallel shards, planned by **one** deterministic
inventory and planner that local runs and CI share. Coverage is unchanged:

- a sharded run executes exactly the tests the serial run of the same selection executes, each
  exactly once, and proves it at run time;
- the full suite stays the full suite;
- there are no FAST/STANDARD/FULL tiers.

A developer can still run any single module, class or test with plain `python3 -m unittest`, and
can run any selection through the new runner.

## Non-goals

- No test tiers, no test removal, weakening or skipping, and no automatic retry of a failed test
  (a retry would hide exactly the timing defects this milestone must surface).
- No change to `controller/` runtime behaviour, **except** two:
  - Design H's ownership-provenance correction (amendment 0). H changes how
    `controller/worker.py` labels an owned process's `source`, and no ownership decision.
  - Design I's drain detach bound (amendment 1): `worker.DRAIN_DETACH_SECONDS` is 10800, not
    600. Only the constant's value changes.
  - The version, the release pipeline's classification, the job-record schema and
    `controller/GENERATION.json` are untouched.
  - The wheel's `controller/worker.py` changes. It ships with the next release, never as a
    re-release of 1.2.0.
  - `tools/` and `tests/` are not in the wheel (`pyproject.toml` `packages = ["controller"]`), and
    `setup.py`'s `DIRTY_SCOPE` is `controller`, `pyproject.toml` and `setup.py`.
- No other Controller change, and no Controller lifecycle refactor: no change to ownership
  membership, adoption, waiting, draining, detaching, re-attaching, recovery or the record
  schema, apart from the value of the drain bound. Designs H and I list what they preserve.
- No other planner change (amendment 2). Design J changes only which estimates may set the local
  largest-atom floor. The parameters, the estimates, the LPT assignment, the plan schema, the
  timing files and everything about CI planning stay as they are.
- No Controller settings or configuration mechanism. The drain bound stays a module constant,
  and making it and the other Controller tunables configurable is deferred (D12).
- No Workflow lifecycle, Harness Adapter or provider redesign, and no Workflow 2.6 compatibility.
- No edit to anything the Workflow Manager owns: `scripts/`, `.claude/commands/`,
  `.workflow-manager/` and `.github/workflows/workflow-conformance.yml`. The frozen conformance
  suites run exactly as that managed workflow runs them (`cd scripts && python3 <file>`).
- No method-level splitting of test classes (see decision D4).
- No change to which tests CI runs where: `tests.test_packaged_runtime` stays in the `package`
  job, and `tests.test_integration_disposable_repo` stays out of CI (live `claude`, real spend).
  See D8.

## Investigation

All numbers were measured at the base commit on 2026-09-26. The reference machine is the
operator's dev box: 16 CPUs, Python 3.14.7, btrfs `/home`, tmpfs `/tmp`. CI is GitHub
`ubuntu-latest` (4 vCPU) with Python 3.12.

### Baseline: local serial

`python3 -m unittest discover -s tests -t .`, driven by a timing wrapper that records each test's
`startTest`-to-`stopTest` duration:

| measure | value |
| --- | --- |
| wall clock | **483.4 s** |
| tests run | 1887 (39 modules, 1887 unique ids, no duplicates, no failed imports) |
| skipped | 8 (all live-gated classes of `test_integration_disposable_repo`) |
| sum of per-test durations | 475 s (fixtures and loading are about 8 s) |
| median test | 7 ms; 1311 of 1887 tests take under 50 ms |
| errors | 1, intermittent: `tests.test_job_validation.CheckpointPredicateUnitTest.test_an_unreadable_post_state_is_not_satisfied`. It passes alone and in its module. The traceback was not captured (see CP5). |

The frozen conformance suites, serial, each as `cd scripts && python3 <file>`: 118.6 s in total.
`workflow_acceptance_matrix_test.py` takes 80.7 s, `workflow_state_test.py` 25.0 s, and the other
five take 0.1 to 4.4 s each.

So a local run of everything CI validates takes about **602 s** serially (483 + 119).

### Where the time goes

The time is dominated by a small number of modules, and by *waiting*, not CPU. The long tests
wait for real supervision windows: settle, grace, drain and quiescence timers against the
`tests/fake_claude.py` worker.

| module | serial seconds | share |
| --- | --- | --- |
| `test_worker` | 150.8 | 32% |
| `test_resume` | 62.7 | 13% |
| `test_lifecycle_orchestration` | 49.5 | 10% |
| `test_job` | 31.6 | 7% |
| `test_pull_request_lifecycle` | 24.9 | 5% |
| `test_job_validation` | 23.5 | 5% |
| `test_trunk_orchestration_e2e` | 22.0 | 5% |
| `test_packaged_runtime` | 19.9 (25.3 with class fixtures) | 4% |
| the other 31 modules | 90 in total | 19% |

The largest *classes* are `test_worker.OwnershipTest` (34.1 s),
`LifecycleBracketSupervisionTest` (33.7 s) and `WrongMatchTest` (33.2 s), then
`test_resume.IncompleteLifecyclePairTest` (19.2 s). The largest single test is 18.1 s
(`test_worker.LifecycleBracketSupervisionTest.test_an_irregular_bracket_matches_nothing_and_the_wakeup_becomes_overdue`).
No class exceeds 35 s.

### Baseline: CI

This is `Main` run `36257702439` at the base commit. `validate`'s critical path is about 3 min 23
s, set by `controller (worker)` (17:04:16 to 17:07:39). The "Controller tests" step of each
hand-curated shard:

| shard | test step | modules |
| --- | --- | --- |
| worker | 193 s | worker, observe, observation_equivalence, fake_claude_contract, worker_stream |
| trunk | 192 s | ten trunk modules |
| cli | 92 s | cli, lifecycle_orchestration |
| job | 76 s | job, job_validation |
| resume | 70 s | resume |
| identity | 30 s | six modules |
| decision | 9 s | six modules |
| docs | 3 s | five modules |

The eight shards hold about 665 s of CI test time but range from 3 s to 193 s. That is a 64x
imbalance, fixed by hand-curation in `tools/ci_workflows.py`'s `CONTROLLER_SHARDS`.

The conformance matrix's longest entry is `workflow_acceptance_matrix_test.py` at 185 s on CI.
`package` took 55 s. Each job adds about 5 to 8 s of checkout, setup-python and editable
install. The earlier run `36253255251` has the same shape: its critical path is `trunk`, at 3 min
5 s from its own start.

Separately, the **managed** `workflow-conformance.yml` runs the same seven suites serially in one
job, in 4 min 40 s to 5 min 25 s per run. It is not this repository's file to change, so it
remains a roughly 5-minute floor on how soon *all* of a pull request's checks can finish,
whatever this milestone does.

### Feasibility: a prototype of the proposed design

A throwaway prototype, not committed, uses the baseline's measured per-class durations. It forms
class-level atoms (whole modules for the two modules with module-level fixtures), assigns them
longest-first to the least-loaded shard, and runs each shard as its own
`python3 -m unittest <names...>` process with its own `TMPDIR`:

| configuration | wall | result |
| --- | --- | --- |
| 8 shards, all 16 CPUs | **63.7 s** (shards 61.1 to 63.6 s, estimate 59.3 each) | union = serial set, 0 duplicates, 0 failures |
| 16 shards, all 16 CPUs | **35.7 s** | union = serial set, 0 duplicates, 0 failures |
| 8 shards on 4 CPUs (`taskset -c 0-3`, CI-like) | 68.4 s | union = serial set, 0 duplicates, 0 failures |
| 12 shards on 4 CPUs | 61.6 s | **1 failure** (below) |

Three conclusions follow:

1. The suite is wait-bound. 8 shards on 4 CPUs is only 7% slower than on 16 CPUs, so a large
   local speed-up is realistic: about 7.5x for the Controller suite at 8 shards.
2. Duration balancing from recorded timings is accurate. Every shard landed within 2.5 s of the
   others and within 7% of its estimate.
3. Oversubscription exposes real latent timing assumptions. The 12-on-4 failure was
   `tests.test_job.DrainDetachJobTest.test_an_unrecognised_escapee_detaches_the_job_and_the_next_step_is_refused`:
   the owned-process snapshot caught the escapee while it was still
   `bash -c (setsid bash -c 'exec -a fake-escapee ...`, before its `exec -a` renamed it, so the
   expected `fake-escapee` name was absent. That is a test-fixture race, not a Controller
   defect. This milestone must fix such races, not hide them (CP5).

The prototype's first attempt also demonstrated a hazard the design must close. Its shard
processes could not import `tests`, and unittest silently substituted a
`unittest.loader._FailedTest` for each name. The shard "ran" one test per name and reported
errors instead of refusing. The run-time equivalence proof (Design E) rejects this by
construction.

### Isolation audit

This is a read-only audit of `tests/`, its fixtures, and the `controller/` paths they exercise.
No test writes to the repository checkout or its Git index. There are no sockets, ports, pid
files or fixed-path locks. Every Controller invocation uses its own `--runtime-dir`, a temporary
checkout or `XDG_STATE_HOME`, or a patched `_dispatch`.

Every `/proc` scan matches by one of:

- a per-job ownership tag (`<timestamp>-<8 hex>` or `launch-<16 hex>`);
- a parent or process-group relation to the scanning process;
- a pid-suffixed argv0.

So one shard's processes can never be mistaken for another's. `lock.probe_lifecycle_lock` matches
`/proc/locks` by device and inode of each test's own temporary directory. Every environment
mutation is restored, and all process-global state (`identity._cached_identity`,
`cli._open_run`, the worker's subreaper refcount) is per process. So shards must be processes,
never threads.

The residual shared resources are:

- `$TMPDIR` (shared by default): harmless, because names are unique;
- `~/.cache/pip`, used by `fixtures.editable_install`'s build-isolated `pip install -e`
  (`tests/fixtures.py:88-98`: no `--no-build-isolation`, no `--no-index`). pip publishes every
  cache entry through `adjacent_tmp_file` plus `replace` (`pip/_internal/network/cache.py`), so
  concurrent shards sharing it is safe. These installs need package-index access: see
  Verification;
- `__pycache__` writes: atomic renames, so safe;
- `$XDG_STATE_HOME` and the home directory: no test writes there, but a regression would.

Two modules define module-level fixtures: `test_release_txn` (a `tearDownModule` that asserts its
argv log is non-empty and contains no forbidden `--force`/`--clobber`/`delete` argv) and
`test_forge` (a `tearDownModule` that asserts no `gh pr merge`/`pr close` ran). Both are safety
assertions. Splitting either module would change that assertion's meaning, so the whole module is
one atom. There are 10 `setUpClass` definitions across 8 modules (`test_release_tools` and
`test_packaged_runtime` have two each), some of them heavily used: wheel builds in `test_packaged_runtime` and
`test_release_tools`, a full `_run` in `test_job_validation`. So a class is the smallest atom.

Timing risk concentrates in the worker-supervision tests. They include unpatched windows of 0.1
to 2 s (`_QUIESCENCE_CONFIRM_SECONDS`, `_NOTIFICATION_CONFIRM_SECONDS`,
`FINAL_EVENT_GRACE_SECONDS`), windows patched down to 1 s, hard wall-clock assertions, and about
40 `timeout=10` worker runs in `test_job`. There is no timeout-scaling knob, and this milestone
does not add one (D6).

### The ownership first-sighting race (CP5 evidence, amendment 0)

CP5 measured this on 2026-09-26. It is kept here as the evidence that motivated amendment 0.

- **Reproduction.** 8 parallel copies of `python3 -m unittest tests.test_worker.OwnershipTest`,
  pinned to one CPU (`taskset -c 0`):
  - 11 failures in 24 runs before CP5's pid-identity fixture fix;
  - 3 failures in 24 after it, all in
    `test_a_setsid_escapee_is_owned_by_tag_and_adopted_and_waited_for` (`'group' != 'tag'`);
  - it never failed in the stress protocol's 26 runs (CP5, CP7).
- **Cause, in the Controller.**
  - `_Ownership.scan` (`controller/worker.py:743-760`) computes every process's basis on every
    scan: `group`, then `tag`, then `adopted`, then the recorded entry.
  - An already-recorded process keeps its first entry (`entry = recorded or {...}`), `source`
    included.
  - The escapee is forked in the worker's group, and calls `setsid(2)` in place only afterwards
    (`tests/fake_claude.py:955-960`).
  - The `bash_bg` step's task change triggers a scan at once (`_transition` then
    `_scan_and_publish`). Under load that first sighting can precede the `setsid`. The entry then
    says `group` for the rest of the process's life, although every later scan finds the process
    by its tag.
- **Not published either.** `_Supervision._signature` (`controller/worker.py:1277-1289`) compares
  owned entries by `(pid, start_ticks)` only. So even a refreshed `source` would be flushed only
  when something else changed.
- **A second cause, in the tests.** `OwnershipTest._escaped` records the *first* observed `ppid`
  (`seen.setdefault`). That can be the escapee's short-lived parent subshell, before the
  subreaper adopts it. The `ppid == os.getpid()` checks of the setsid and reparented tests have
  this shape.
- **The same latent shape, never failed.** It also sits under:
  - `tests.test_resume.DrainDetachedReattachTest`'s `source` checks
    (`tests/test_resume.py:3201,3225`);
  - `OwnershipTest.test_a_leaked_inner_anchor_holds_the_outer_job_only_until_it_ends_itself`
    (`tests/test_worker.py:2466`).
- **Why the tests cannot fix it.** Every stage of a process the worker spawns is visible from its
  fork (CP5 notes), so no fixture can make the first sighting come after the `setsid`. The only
  test-side alternatives were to patch the supervisor's scan, or to stop proving tag ownership.
  Plan G reserves this case for an amendment.
- **`source` is provenance only.** No `controller/` code path branches on it. Its only reads are
  `worker.py:750` (the recorded fallback) and `worker.py:1560` (the re-attach seed). Owning,
  waiting, draining, ending and reaping use the owned set, `outside_group` (derived from `pgrp`)
  and `(pid, start_ticks)` identity. So the defect mislabels a process, and never changes what
  is owned.

## Invariants

- **I1 Equivalence.** For any selection `S` and any shard count `N`, the plan's shards partition
  `S`: their union is `S`, they are pairwise disjoint, and none is lost or added. This holds for
  every possible timing input, including a missing or corrupt timing file.
- **I2 Proof at run time, not by construction alone.** Every shard reports the exact test ids it
  executed. The aggregate result is `PASS` only if the multiset of reported ids equals `S`
  exactly. A missing, extra, duplicated or substituted id (for example unittest's `_FailedTest`)
  is a failure of the whole run, never a pass.
- **I3 Timing is advisory.** Timing data changes only *where* a test runs and how many shards
  there are, never *whether* it runs. No code path reads timing data to include, exclude, skip or
  order-dependently condition a test.
- **I4 Determinism.** Given the same repository content, selection, profile, shard count and
  timing file, every process computes byte-identical plans, carrying the same `plan_digest`. CI
  shard jobs recompute the plan and refuse to run on a digest mismatch.
- **I5 Serial reference.** `--serial` runs the same selection, in the same canonical order,
  through the same executor and the same environment shaping, in one process. It is the
  reference that I1 and I2 compare against.
- **I6 No hidden retries.** A failed test is reported failed. The runner never re-runs it.
- **I7 Existing entry points keep working.**
  - `python3 -m unittest discover -s tests -t .`, `python3 -m unittest <module/class/test>` and
    `cd scripts && python3 <suite>` behave exactly as today.
  - The new runner is additive.
  - The inventory is defined as what that `discover` call loads, plus the managed conformance
    list.
- **I8 Isolation.** Shards are separate processes, each in its own session. Each has its own
  `TMPDIR` and `XDG_STATE_HOME`, and an environment marker that identifies its descendants.
- **I9 Explicit serialization.** A test needs to run alone only if a reviewed registry entry,
  with a reason, says so. Nothing relies on accidental ordering or placement.
- **I10 Provenance, not membership** (amendment 0). Design H changes only the `source` label of
  an entry that is already owned, and when that label is published. It never changes which
  processes are owned, or anything that is decided from the owned set.

## Design

All new code is stdlib-only Python under `tools/`, like `tools/ci_workflows.py` and
`tools/release.py`:

- `tools/test_shards.py`: the library (inventory, selection, timing model, planner, executor
  protocol and aggregation);
- `tools/run_tests.py`: the operator CLI.

Neither is imported by `controller/`.

### A. Inventory and selection (CP1)

**Families.** The inventory has two families. Test id syntax differs between them, so the two can
never collide.

- `controller`: every test that `unittest.TestLoader().discover("tests", top_level_dir=<repo>)`
  loads, identified by `TestCase.id()` (for example
  `tests.test_worker.OwnershipTest.test_x`). Discovery errors refuse the whole inventory with a
  named error, and are never planned around. They include `loader.errors`, any
  `unittest.loader._FailedTest`, `ModuleImportFailure` or `_ErrorHolder` in the flattened suite,
  any `unittest.loader.ModuleSkipped` (a module that raises `SkipTest` while being imported,
  LP-R2-002), and any duplicate id. `ModuleSkipped` is refused rather than mapped because its id
  (`unittest.loader.ModuleSkipped.<module>`) is synthetic and the module's real tests are never
  loaded, so no inventory can name them; no module in `tests/` does this today, and a module that
  needs to skip wholesale raises `SkipTest` from `setUpModule` instead, which the recording rules
  handle.
- `conformance`: one test id per frozen suite, `conformance:<file>` (for example
  `conformance:workflow_state_test.py`). The list is read from the managed
  `.github/workflows/workflow-conformance.yml`'s `run: python3 <file>` lines, the same source
  `test_ci_workflows` already checks. It moves from `tools/ci_workflows.py`'s hand-kept
  `CONFORMANCE_SUITES` into the inventory, so it has exactly one source.

**Atoms.** An atom is the unit of placement, and it is never split:

- a `TestCase` class, keyed `<loading module>.<ClassName>`;
- the whole module, when the module defines `setUpModule` or `tearDownModule`. This is detected
  from the loaded module object, not from a hand list. Today that means `test_release_txn` and
  `test_forge`;
- a conformance suite file.

Class-level fixtures (`setUpClass`) therefore run exactly once per run, as they do serially.

**Canonical order.** The canonical order is exactly `discover`'s order: module files sorted,
classes in `dir()` order, methods sorted by the loader. Conformance suites follow, in managed-file
order. Within a shard, atoms run in canonical order, so a shard is an order-preserving
*subsequence* of the serial run.

**Selection.**

- No names selects the **full** selection: every controller test plus every conformance suite.
  That is `discover` plus conformance, a superset of today's `discover`-only local habit.
- Otherwise each name is resolved and the results are unioned:
  - a unittest dotted name (`tests`, `tests.test_worker`, `tests.test_worker.OwnershipTest`,
    `tests.test_worker.OwnershipTest.test_x`), by prefix match over controller ids;
  - `conformance` (all suites) or `conformance:<file>`.
- A name that matches nothing refuses, naming it.
- A partial selection inside a class or a module-fixture module selects those ids only. The atom
  is then the selected subset, which still runs in one process in canonical order.

Selection is set semantics over canonical ids. The runner prints the selected count, and records
the full id list in `plan.json`.

**Placement (CI only).** `CI_PLACEMENT` in `tools/test_shards.py` maps a small number of modules
out of the CI shard matrix. It replaces `tools/ci_workflows.py`'s `PACKAGE_TEST_MODULES` and
`EXCLUDED_TEST_MODULES`:

- `tests.test_packaged_runtime` goes to `package`. It needs the wheel that job builds, and runs
  there under `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`;
- `tests.test_integration_disposable_repo` is `excluded`, with the existing live-`claude` reason.

The CI selection is the full selection minus placed modules. A test asserts that the inventory is
partitioned exactly into CI shards, `package` and `excluded`. This replaces
`CoverageTest.test_every_test_module_is_placed_exactly_once`. Local full runs keep both modules,
exactly as `discover` does today.

### B. Timing data (CP2)

**Result record.** Each shard writes `shard-<i>.json`:

- `schema_version`;
- `plan_digest`;
- `shard`;
- `argv`;
- `started_at` and `ended_at`, as ISO strings;
- `wall_seconds`;
- `exit_status`;
- `tests`: an ordered list of
  `{id, outcome ∈ {pass, fail, error, skip, expected_failure, unexpected_success}, seconds, detail?}`,
  exactly one entry per planned id (see "Recording rules" below). `detail` holds the traceback(s)
  or skip reason;
- `fixture_errors`: an ordered list of `{description, traceback}`, one per module-, class- or
  cleanup-level fixture error that unittest reports through an `_ErrorHolder`. `description` is
  the holder's own description, for example `tearDownModule (tests.test_release_txn)` or
  `setUpClass (tests.test_packaged_runtime.WheelTest)`. These entries are never test ids;
- `fixture_skips`: an ordered list of `{description, reason}`, one per `setUpClass` or
  `setUpModule` that raised `SkipTest` (LP-R2-001), with the same `_ErrorHolder` description.
  These are never test ids either, and they never affect the verdict;
- `atoms`: `{atom: seconds}`, measured from the atom's first `startTest` to its last `stopTest`,
  plus its class fixtures;
- `leaked_processes`.

A conformance atom records a single test with the suite's wall time and outcome (its exit
status), and its log holds the suite's own unittest output.

**Recording rules** (LP-R1-001). The recording result keys every event to a planned id or to a
fixture, never to an id unittest synthesises. Each rule below was checked against the unittest
version on the reference machine (Python 3.14.7) with a probe module:

- **Fixture errors.** An `addError`/`addFailure` whose test is a `unittest.suite._ErrorHolder`
  (module or class setup, teardown or cleanup) is appended to `fixture_errors`, never to `tests`.
  So it can neither vanish nor surface as an unplanned id.
- **`setUpClass` failure.** unittest then never calls `startTest` for that class's tests. After
  the suite finishes, every planned id that has no entry yet and belongs to a class (or module)
  with a recorded setup fixture error gets an `error` entry with `seconds: 0` and a `detail` that
  names the fixture error. The coverage stays exact, and the shard is `FAIL`, not NOT RUN.
- **Fixture skips** (LP-R2-001). A `setUpClass` or `setUpModule` that raises `unittest.SkipTest`
  reaches `addSkip`, not `addError`, with an `_ErrorHolder` described `setUpClass
  (<module>.<Class>)` or `setUpModule (<module>)`; none of that class's or module's tests is
  `startTest`ed, and `wasSuccessful()` stays True (probe-verified, Python 3.14.7). An `addSkip`
  whose test is an `_ErrorHolder` is appended to `fixture_skips`, never to `tests`. After the
  suite finishes, every planned id that has no entry yet and belongs to that class (or module)
  gets a `skip` entry with `seconds: 0` and a `detail` naming the holder and its reason. Coverage
  stays exact and the shard is `PASS`, exactly as serial `unittest`. This is not hypothetical:
  `tests/test_release_tools.py:80-85,328-333` and `tests/test_packaged_runtime.py:169-178` (and
  its subclass at `:543`) skip this way whenever `fixtures.wheel_build_prerequisite()`
  (`tests/fixtures.py:100-127`) reports a missing piece, which is the case in CI's test jobs
  (`validate.yml` installs `setuptools>=70.1` only in the `package` job). An `addSkip` on any
  other non-planned test object is a coverage violation, never silently kept.
- **Subtests.** `addSubTest(test, subtest, err)` is folded into `test.id()`, the parent. A test
  reports exactly one entry: `error` if any subtest errored, else `fail` if any subtest failed,
  else the outcome of the test itself. Each failing subtest's own id (`<id> (<params>)`) and
  traceback are appended to the parent's `detail`. A failing subtest produces no `addSuccess` for
  its parent, so the entry is written at `stopTest`, never at `addSuccess`.
- **`unexpected_success`** is a failure, because since Python 3.11 `wasSuccessful()` is False
  when there is one. It makes the shard `FAIL` (exit 1).
- **Consistency** (two-sided since LP-R2-001). The shard's verdict is computed from its record,
  and is `PASS` only if every entry's outcome is in `{pass, skip, expected_failure}` and
  `fixture_errors` is empty. The executor asserts that the controller-family part of this verdict
  **equals** the in-process `unittest` result's own `wasSuccessful()`, in both directions (an
  empty controller suite counts as `wasSuccessful()` True; conformance atoms are judged by their
  exit status, which has no in-process counterpart). A mismatch either way refuses the shard
  (exit 2, naming both values), so any `unittest` event shape these rules do not map surfaces as
  a named refusal rather than as a silent misclassification. A shard is never PASS where serial
  `unittest` would fail, nor FAIL where it would pass.

**Timing files.** Both files use the same schema:

```json
{"schema_version": 1, "profile": "ci",
 "atoms": {"tests.test_worker.OwnershipTest": {"seconds": 47.9, "samples": 3, "tests": 9}},
 "updated_from": ["<plan_digest>", "..."]}
```

- **Committed CI profile, `tools/test_timings.json`.** This is the only input CI planning reads.
  It is seeded in CP4, once the runner exists, from a `--serial` run on the reference machine
  through `timings merge`, with `profile: "seed-local"`. It is refreshed from a real CI run's
  aggregate artifact during functional review (done in round 1, F1 `9501fbb`: the file is now
  `profile: "ci"`, merged from 5 CI runs). Nothing writes
  it except the explicit command below. CI never writes the repository.
- **Local profile, `$XDG_CACHE_HOME/workflow-controller-tests/timings-local.json`.** The fallback
  is `~/.cache/...`, keyed by nothing else, because it is advisory. It is updated automatically
  after every local run, whatever its outcome, from the atoms that completed. It is never
  committed and is outside the repository, so there is no `.gitignore` change. Every write, to
  either profile, is write-to-an-adjacent-temporary-file, `fsync`, then `os.replace`
  (LP-R1-006), so two concurrent local runs can lose one run's update but never tear the file.
  The corrupt-file fallback below remains the backstop, not the normal path.

**Update policy.** `seconds_new = 0.5·observed + 0.5·seconds_old`, and `samples` increments.

- An atom whose test count changed takes the observed value outright.
- An atom that is absent from the inventory is pruned on update.
- An atom that failed or was interrupted is not merged, because its duration is not
  representative.
- An atom with a fixture skip (LP-R2-001) is not merged either: its near-zero duration would
  teach the profile, for example, that `test_release_tools`'s wheel-build classes are free on CI.

Refreshing the committed CI profile is an explicit, reviewed act:

`python3 tools/run_tests.py timings merge --into tools/test_timings.json <aggregate-dir>...`

A sustained drift is not a correctness problem (I3). It can cost wall time, through the shard
count and the balance, but never coverage.

**Estimates.**

- A known atom uses its recorded seconds.
- An unknown atom (new tests, no history) is estimated as `tests_in_atom × mean_seconds_per_test`.
  The mean is taken over that atom's module in the profile, else over the whole profile, else
  from a fixed default of 0.25 s per test, the baseline mean (475 s / 1887).
- A conformance atom without history uses 30 s.
- Local planning uses the local profile, falling back to the committed CI profile per atom.
  The fallback estimate counts in the total and in the assignment. It never sets the local
  largest-atom floor (Design J, amendment 2): CI seconds are not local seconds (the acceptance
  matrix is about 207 s on CI and 81 s locally), and a CI-sized floor shrinks the local shard
  count. Mixing profiles can change the shard count and the balance, never I1.

**Failure tolerance.** A timing file that is missing, unreadable or schema-invalid is replaced
by the defaults with one warning line naming the file. The same holds for any file whose
entries are not finite and non-negative. None of these fails the run (I3). A test pins that a
corrupt timing file yields a valid partition.

### C. Adaptive shard planning (CP3)

```
total      = Σ estimate(atom)                      over the selected atoms
largest    = max estimate(atom)                    over the floor atoms (below; 0 if there are none)
effective  = max(target_shard_seconds, largest)   # no shard can be shorter than its largest atom
N          = clamp(ceil(total / effective), min_shards, max_shards)
N          = min(N, number_of_atoms)               # never an empty shard
```

`--shards N` pins the count. A selection with one atom plans one shard.

**Floor atoms** (amendment 2, Design J). For the `ci` profile, every non-exclusive selected
atom is a floor atom, exactly as before. For the `local` profile, a floor atom is one whose
estimate comes from a local-machine timing profile: the first timing file that records the atom
is not the committed `tools/test_timings.json`. An atom that is estimated from the committed CI
profile, or from a per-test mean or a default because no file records it, still counts in
`total` and in the assignment, but not in `largest`. With no local profile at all, `effective`
is the target.

Parameter values, justified by the Investigation:

| profile | `target_shard_seconds` | `min_shards` | `max_shards` | resulting N at the base commit |
| --- | --- | --- | --- | --- |
| `local` | 60 | 2 | `min(8, os.cpu_count())` | total ≈ 594 s, largest = conformance acceptance matrix ≈ 81 s → effective 81 → `ceil(594/81)` = 8 → **8** |
| `ci` | 180 | 2 | 16 | total ≈ 665 + 344 ≈ 1009 s (CI seconds), largest ≈ 185 s → effective 185 → **6** |
| `local`, no local profile (amendment 2) | 60 | 2 | `min(8, os.cpu_count())` | total ≈ 968 s from the committed CI profile, no floor atom → effective 60 → `ceil(968/60)` = 17 → **8** |

The prompt's suggested starting points were a 240 s target, a minimum of 2 and maxima of 8 local
and 16 CI. The measurements refine them:

- **Local 240 s would plan 3 shards** (about 200 s wall) against the measured 64 s at 8 shards.
  That throws away the realistic local win, so the local target is 60 s.
- **CI at 240 s would plan 5 shards of about 200 s.** With the added plan and aggregate jobs, that
  makes `validate` slower than today's 3 min 23 s. 180 s is about the unavoidable 185 s
  conformance floor, so any smaller target only adds jobs, and billing is per job-minute, without
  shortening the path.
- **The local maximum is bounded by `os.cpu_count()`**, because the prototype's 12-on-4-CPU run
  failed where 8-on-4 passed. One shard per CPU is the measured-safe default. `--shards` and
  `--jobs` let an operator deliberately go beyond it, with a warning.

The parameters live as named constants in `tools/test_shards.py`. `run_tests.py` exposes
`--target-seconds`, `--min-shards`, `--max-shards` and `--shards`. There is no environment
variable or config file, so there is less configuration surface.

**Assignment (deterministic LPT).**

1. Sort atoms by `(-estimate, canonical_index)`.
2. Assign each atom to the shard with the minimum `(load, shard_index)`.
3. Within each shard, reorder its atoms by canonical index.

Floating-point estimates are first rounded to milliseconds, as integers, so every platform
compares identically. LPT's worst case is within 4/3 of optimal. The prototype balanced to within
4%.

**Plan and digest.** `plan.json` holds:

- `schema_version`;
- `profile`;
- `selection_names`;
- `selected_ids`, in canonical order;
- the parameters;
- `N`;
- `shards`, each `{index, atoms, test_ids, estimate_seconds}`;
- `timing_source`, as a repository-relative POSIX path plus a SHA-256, or `"defaults"`. A timing
  file outside the repository (the local profile) is recorded as `"local-profile"` plus its
  SHA-256, never as an absolute path, so the digest never depends on where the checkout or the
  cache lives (LP-R1-003);
- `plan_digest`, the SHA-256 of the canonical JSON of everything else.

The parameters that enter the digest are the planning inputs: profile, target, minimum and
maximum, and a pinned `--shards` value only if the plan was itself computed with one. The
resulting `N` is in the plan as an output, not a parameter.

The planner validates I1 on the plan it has just built (union, disjointness and count) before it
returns, and raises otherwise. That is a second line of defence behind the property tests.

### D. Executor and local runner (CP4)

**Shard executor** (`run_tests.py exec-shard`). It is used identically locally and in CI, and
takes the plan in exactly one of two forms (LP-R2-003); giving both, or neither, refuses with
exit 2:

- **a recorded plan**: `exec-shard --plan plan.json --shard i --results-dir D`, used by the local
  runner and by `--replay`;
- **the planning inputs**: `exec-shard --profile ci --ci-placement --shard i --expect-digest
  <digest> --results-dir D`, used by CI's `tests` jobs (F). The executor recomputes the plan with
  the same code `plan` uses, and refuses (exit 2) unless its `plan_digest` equals
  `--expect-digest`. It does not write a `plan.json`: the only `plan.json` `aggregate` ever reads
  is the `plan` job's.

Either way, every `shard-<i>.json` carries the `plan_digest` it ran, and `aggregate` refuses
(exit 2) any result whose digest differs from its `--plan`'s.

1. Load (or recompute and digest-check) the plan, and take this shard's atoms.
2. Build a unittest suite with `loadTestsFromName` per controller atom, filtered to the planned
   ids.
3. **Before running anything, compare the loaded ids with the planned ids.** Any difference, and
   any `_FailedTest`, `ModuleImportFailure` or `ModuleSkipped`, refuses the shard with exit 2, naming the
   difference. This closes the prototype's `_FailedTest` hazard.
4. Run the controller tests in canonical order with a recording `TextTestResult`, at verbosity 2,
   into `shard-<i>.log`.
5. Run each conformance atom as `subprocess.run([sys.executable, "<file>"], cwd="scripts")`, with
   its output appended to the log. This is the managed workflow's command (`cd scripts && python3
   <file>`), with three deliberate differences (LP-R1-004):
   - the interpreter is the runner's own `sys.executable`, chosen so a local run never picks up a
     different `python3` from `PATH`. In CI both are setup-python's 3.12;
   - it runs after the shard job's `pip install -e .`, which today's `conformance` job
     (`.github/workflows/validate.yml:47-67`) does not do. The suites import only `scripts/`
     modules, so the installed `controller` package is not on their path of use;
   - it inherits the shard's `TMPDIR`, `XDG_STATE_HOME`, `PIP_CACHE_DIR` (if set) and marker.
     The suites build scratch repositories under `TMPDIR`, so this changes only where.

   The managed `workflow-conformance.yml` still runs every suite in exactly its own environment on
   every pull request, so any divergence these differences caused would show as a disagreement
   between the two workflows.
6. Write `shard-<i>.json`.

The process starts with `SIGINT` at `SIG_DFL` and `sys.path[0]` set to the repository root, as
`python -m unittest` sets it. The SIGINT reset matters: a shell's `&` leaves SIGINT ignored, the
Controller children the tests spawn inherit that, and `tests.test_cli.RunRecordCtrlCTest` and
`tests.test_resume.InterruptWhileWaitingTest` then fail spuriously (observed 2026-09-26).

**Environment shaping.** The parent sets these for each shard, and for `--serial`, which is
treated as shard 0 of 1, so I5 holds:

- `TMPDIR=<results>/shard-<i>/tmp`;
- `XDG_STATE_HOME=<results>/shard-<i>/state`;
- `PIP_CACHE_DIR` is **not** set by default: shards share the operator's pip cache, which is
  concurrency-safe (see the Isolation audit), and a warm cache keeps the editable-install tests
  from re-downloading their build requirements on every run (LP-R1-002). `--isolated-pip-cache`
  opts in to `PIP_CACHE_DIR=<results>/pip-cache-<i>`, a cold per-shard cache, for diagnosing a
  suspected cache interaction;
- `WORKFLOW_CONTROLLER_TEST_SHARD=<run_id>/<i>`: the leak marker.

Nothing else is changed. `WORKFLOW_CONTROLLER_HOME`, `HOME` and `PATH` are inherited unchanged,
because several tests depend on stripping or inheriting them. `PYTHONPATH` is inherited
unchanged too. The runner never sets it: `PYTHONPATH=.` makes
`tests.test_identity.DecoyAndRealRouteTest` import its decoy package and fail.

**Leak detection.** After a shard exits, the parent scans `/proc/*/environ` for its marker. It
reports every surviving process (pid, argv, age) in the shard's `leaked_processes` and in the
summary, and then kills each one's process group. A descendant started with `env -i` loses the
marker and is invisible to this scan. That is a documented limitation: the only such processes
the suite creates are deliberate escapee fixtures, which reap themselves. A leak is reported as a **warning**, not a
failure. This milestone makes it visible, and CP5 triages any it finds. Making it a failure is
D7.

**Local runner.**

| command | what it does |
| --- | --- |
| `python3 tools/run_tests.py` | full selection, planned `local`, runs all shards concurrently, one summary |
| `python3 tools/run_tests.py tests.test_worker tests.test_cli.RunRecordCtrlCTest` | the same for a selection |
| `python3 tools/run_tests.py --serial [names]` | the same selection, in one process, in canonical order (I5) |
| `python3 tools/run_tests.py --plan-only [names]` | prints the plan (shards, estimates, largest atom, digest) and exits 0 |
| `python3 tools/run_tests.py --replay <results>/plan.json [--shard i]` | re-runs a recorded plan, or one shard of it, exactly |
| `--jobs J` | at most `J` shards at once. The default is `N`; lower values queue shards |
| `--results-dir D` | default: `$TMPDIR/workflow-controller-tests/<run_id>/` |
| `-v` | streams each shard's per-test lines, prefixed with `[i]` |
| `python3 tools/run_tests.py timings merge ...` | the explicit committed-profile refresh (B) |

Each shard runs in its own session. Ctrl-C to the runner sends SIGTERM and then SIGKILL to every
shard's process group within 5 s. A shard killed this way is reported as `INTERRUPTED`, never as
passed. The runner exits 130.

### E. Aggregation and diagnostics (CP4)

`aggregate(plan, shard_results)` is shared by the local runner and the CI `tests-result` job.

- **Verdicts.** Each shard is `PASS`, `FAIL` (a failing or erroring test, an unexpected success,
  or any `fixture_errors` entry), `CRASHED` (the
  process ended without a result file, or with a result file that does not cover its planned
  ids), `REFUSED` (an executor precondition failed, exit 2) or `INTERRUPTED`.
- **Coverage check (I2).**
  - Reported ids must equal `selected_ids` as multisets.
  - The ids a `CRASHED` or `INTERRUPTED` shard did not report are listed as **NOT RUN**, by name.
  - A duplicate or unplanned id is listed as a coverage violation.
- **Exit status.**

  | exit | meaning |
  | --- | --- |
  | `0` | every shard `PASS` and coverage exact |
  | `1` | any test failure, error or unexpected success, or any fixture error |
  | `2` | refused, crashed, not-run or a coverage violation |
  | `130` | interrupted |

  A skip and an expected failure are not failures, exactly as in `unittest`.
- **Summary.** The summary is a table, one row per shard, with index, verdict, test count, wall
  seconds and estimate seconds. Then, for each failing test:
  - its id, shard and outcome;
  - the last 40 lines of its traceback;
  - the log path (`<results>/shard-<i>.log`);
  - two reproduction commands:
    - `python3 -m unittest <id>`, the isolated test;
    - `python3 tools/run_tests.py --replay <results>/plan.json --shard <i>`, the exact
      co-resident order, for an order-dependent failure.

  Each fixture error is listed the same way, under its holder description (for example
  `tearDownModule (tests.test_release_txn)`), with its traceback and the same log path and
  reproduction commands, using the module or class as the `unittest` name.

  Fixture skips are not failures, but are listed in one informational block, each holder's
  description with its reason and the number of planned ids it skipped, so a CI run that skipped
  the wheel-build classes says so on its summary page.

  The summary also lists the wall time, the largest atom, and the balance ratio
  `max(shard wall) / mean(shard wall)`.
- **Files.** The same summary is written to `<results>/SUMMARY.md` and, in CI, to
  `$GITHUB_STEP_SUMMARY`.

### F. CI integration (CP6)

`tools/ci_workflows.py`'s `validate.yml` model changes. `ci.yml` and `main.yml` are unchanged,
except that `validate`'s internal job names change.

```
plan          checkout, setup-python 3.12, pip install -e . (the shard jobs' import environment);
              python3 tools/run_tests.py plan --profile ci --ci-placement --github-output
              -> outputs: shards='[0,1,...,N-1]', count=N, digest=<plan_digest>; uploads plan.json
tests         needs plan; matrix shard: fromJSON(needs.plan.outputs.shards); fail-fast: false
              checkout, setup-python, pip install -e .;
              python3 tools/run_tests.py exec-shard --profile ci --ci-placement
                  --shard ${{ matrix.shard }}
                  --expect-digest ${{ needs.plan.outputs.digest }} --results-dir results
              upload-artifact results-<shard> (if: always())
tests-result  needs [plan, tests]; if: always()
              download the plan job's plan.json and all results-*;
              python3 tools/run_tests.py aggregate --plan plan.json results-*
              -> $GITHUB_STEP_SUMMARY, aggregate timings artifact, exit status per E
package       unchanged: builds the wheel, verify-wheel --local, the package-placed modules under
              CONTROLLER_REQUIRE_PACKAGING_TESTS=1, pipx smoke
```

- **Plan recomputation.** Each `tests` job recomputes the plan from the checked-out commit and
  the committed `tools/test_timings.json`, with exactly the `plan` job's planning parameters
  (`--profile ci --ci-placement`, no `--shards`), and refuses to run unless its digest equals the
  `plan` job's (I4). There is no `--count` argument (LP-R1-003): `N` is part of the digested
  plan, so a digest match already proves the counts agree, and a separate pinned count could
  only make the two plans' inputs differ.
- **Why recompute.** The matrix carries only indices, not id lists, which avoids GitHub's output
  size limits and unreadable job names.
- **Replacements.** The separate `conformance` matrix job is gone: conformance atoms are planned
  with everything else. The hand-curated `CONTROLLER_SHARDS` is deleted.
- **Required check.** `tests-result` is the job whose status represents the Controller and
  conformance validation. A `tests` job that never started (cancelled or infrastructure failure)
  leaves missing result artifacts, which `aggregate` reports as NOT RUN, exit 2. If `plan`
  itself failed, there is no `plan.json`, and `tests-result` fails with exit 2 and says so. It
  never passes vacuously.
- **Timing refresh.** `tests-result` uploads a `timings-ci` artifact: the merged per-atom
  durations of that run. `timings merge` consumes it for the explicit refresh.
- **Actions.** `validate.yml` keeps its existing major-tag action references (ADR 0002). The
  steps use only `actions/checkout`, `setup-python`, `upload-artifact` and `download-artifact`,
  which `main.yml` already pins by SHA. The pins stay as they are.
- **Check names.** The checks become `validate / plan`, `validate / tests (<i>)`,
  `validate / tests-result` and `validate / package`. The README's branch-protection note is
  updated (branch protection is not configured today).

`tests/test_ci_workflows.py` is updated to assert:

- the new job graph and the matrix expression;
- that the `exec-shard` argv carries `--expect-digest`;
- that `tests-result` runs `if: always()` and needs `tests`;
- that the conformance family equals the managed workflow;
- the placement partition.

`--check` keeps GitHub running exactly the model.

**Expected CI effect, stated honestly.**

- **`validate`'s critical path.** Today it is about 3 min 23 s. The Controller part falls from
  193 s to the planner's balance point. The whole path is then bounded below by the conformance
  acceptance matrix atom (about 185 s), plus one job's setup, the plan job (about 10 to 15 s) and
  the aggregate job (about 10 to 15 s).
- **The projection is about 3.5 to 4 min, roughly neutral against today's.** The CI gain is not
  the headline of this milestone. Its value is that the matrix is computed rather than
  hand-curated, stays balanced as tests are added, and proves coverage. The ~5 min target is
  met, and it already is.
- **The whole-PR floor.** The managed `workflow-conformance.yml` (about 5 min) remains the floor
  on when all of a pull request's checks finish.
- **The headline is local.** The whole CI-equivalent local run takes about 602 s serially,
  projected to about 80 to 90 s at 8 shards.

### G. Serialization registry and timing-flake hardening (CP5)

`EXCLUSIVE_ATOMS: dict[str, str]` in `tools/test_shards.py` maps an atom to the reason it must
run alone.

- **Locally**, exclusive atoms run after all parallel shards have finished, one at a time, in one
  final `exclusive` shard, with nothing else running.
- **In CI**, every job is its own VM and runs its atoms serially, so the planner places exclusive
  atoms together in one shard, which is sufficient.
- **Checks.** A test asserts that every entry names an existing atom and has a non-empty reason.
  The runner prints the registry's size in every summary, so the list is auditable.
- **Contents.** It starts **empty**. An entry is admissible only for a demonstrated need that
  cannot be fixed in the test, recorded with its evidence (D5).

**Hardening policy.**

- A timing flake exposed by parallel load is fixed by making the test wait for the condition it
  depends on. Example: wait until the escapee's `/proc/<pid>/cmdline` reads `fake-escapee`
  before the worker exits, not "sleep 0.3 and hope".
- The fix never loosens an assertion, lengthens a Controller window, patches a window
  differently from what the test documents, or adds a retry.
- Each fix is a separate, named change in CP5's notes, with the stress run that exposed it and
  the stress run that clears it.

**Known candidates.**

1. The escapee `exec -a` race in `test_job.DrainDetachJobTest` (measured above).
2. The intermittent `CheckpointPredicateUnitTest` error (measured above). CP5 first captures its
   traceback with the executor's recording result.
3. `test_worker.InterruptedTest.test_hanging_worker_with_short_timeout_classifies_interrupted_and_reaps_group`,
   observed to flake under full-suite load (3 of 13 runs on 2026-09-24).
4. Whatever the CP5 stress protocol exposes.

**Stress protocol** (CP5, re-run at CP7):

- 5 consecutive full local runs at defaults (8 shards on 16 CPUs);
- 5 full runs at 8 shards under `taskset -c 0-3`;
- 3 full runs at `--shards 12 --jobs 12` under `taskset -c 0-3` (the oversubscription that
  exposed candidate 1).

The pass bar is zero failures across all runs. Every failure is triaged into a fixed test
defect, a genuine Controller defect (stop, and raise a plan amendment: this milestone does not
change `controller/`), or, only with evidence, an `EXCLUSIVE_ATOMS` entry. Amendment 0 is the one
use of the second path so far. Its Controller change is Design H, and any further genuine
Controller defect still needs its own amendment.

### H. Ownership provenance follows the current basis (CP5B, amendment 0)

This section fixes the defect described in "The ownership first-sighting race" (Investigation).
The manual external implementation review's I1 accepted either an amendment that defers the
race or one that fixes it. This one fixes it (D11).

**Rule.** An owned process's `source` says why the **latest** scan that found it owns it, not
why the first one did.

1. **The scan.** In `_Ownership.scan` (`controller/worker.py`), a recorded entry whose identity
   still matches becomes `dict(recorded, source=source)`, not `recorded`. `source` is computed
   exactly as today, in the same order: `group`, then `tag`, then `adopted`, then the recorded
   entry's own `source`. So a process found only through its record keeps the label it had.
   A new entry is built exactly as today.
2. **The publication.** `_Supervision._signature` compares owned entries by
   `(pid, start_ticks, source)`, not `(pid, start_ticks)`. A relabel is then flushed by the next
   scan, instead of waiting for an unrelated change. A label changes only when a process's real
   basis changes, for example at its `setsid`, and a process cannot rejoin the worker's group
   once it has left the session. So this adds at most one or two publications per escapee.

**What H preserves**, each checked by a CP5B test or an existing one:

- **Membership.** Which processes are owned, the order of the bases, pid-reuse detection
  (`start_ticks`), daemon exclusion, `outside_group` and the unverifiable-scan fallback are
  unchanged. No `controller/` code path branches on `source` (Investigation), so waiting,
  draining, detaching, ending, reaping, re-attaching and recovery are unchanged too.
- **First-sighting fields.** `cmdline` stays the first-seen command line. The external review
  ratified the assertions that rely on that (IR1-002). `owned_processes_seen`'s `count` and
  `sample` stay first-sighting history: each `sample` item is a copy taken when the identity was
  first seen.
- **The record schema.** Same keys, and the same `source` vocabulary (`group`, `tag`, `adopted`,
  and `recorded` for a re-attach seed that had none). A re-attaching Controller
  (`_recorded_ownership`) seeds from the record as today. Its scans now relabel a seeded entry
  that they find by group or tag, for example a legacy `recorded` entry that carries the tag.
- **Everything else.** H leaves the drain bound (amendment 1 changes it separately, Design I),
  `--timeout`, the scan cadence (`_OWNERSHIP_SCAN_SECONDS`) and every other window unchanged.
  There is no new setting, and `controller/GENERATION.json` is not bumped, because no handoff or record contract changes.

**Test-side corrections.** These fix the second, test-side cause under G's hardening policy.
They do not loosen any assertion.

- `OwnershipTest._escaped` records the **last** observed `ppid` of each owned process, not the
  first. This is deterministic. The `bash_bg` command runs the `( ... & )` spawn subshell in the
  foreground, so the subshell has exited, and the escapee has been reparented, before the step
  ends. That happens before the worker exits, so before every `DRAINING` publication.
- `OwnershipTest._orphan` returns the escapee's entry from the **last** `DRAINING` publication
  that lists it, not the first. With H, that entry's `source` is the escapee's basis after its
  `setsid`.
- The existing assertions stay as they are: `source == "tag"` for the setsid escapee, `"group"`
  for the reparented one, and `ppid == os.getpid()` (or `!=` without a subreaper).

**Deterministic reproduction.** Stress only makes the race likely, so it is not enough.
`tests/fake_claude.py`'s `bash_bg` gains an `orphan_gate` option, a FIFO path. The escapee
blocks on `read _ < "$gate"` while it is still in the worker's group, and only then execs
`setsid`. That is a shell builtin, so there is no extra child and the pid does not change. The
test:

1. waits until a publication lists the gated escapee with `source: "group"`, which is the first
   sighting the race produced by chance;
2. opens the FIFO to release it;
3. asserts that a later publication lists the same `(pid, start_ticks)` with `source: "tag"`,
   and that `cmdline` is still the first-seen value.

Without H, step 3 fails every time.

### I. The drain detach bound (CP5C, amendment 1)

**Problem.** After the worker process exits, the supervisor waits for the processes it still owns
for at most `worker.DRAIN_DETACH_SECONDS`, then detaches (plan decision 9 of the worker-lifecycle
milestone, ADR 0004). The bound is 600 s. The operator has reproduced, several times in
`workflow-manager`, legitimate owned background work (verification the worker started in the
background) that runs well over 10 minutes after the worker exits. At 600 s the Controller
detaches, exits 45 and leaves the job `LAUNCHED` at `DRAINING`, so a valid run needs a manual
`resume` or is interrupted.

**Change.** `DRAIN_DETACH_SECONDS = 10800` (3 hours) in `controller/worker.py`, as an interim
bound for current and near-term runs. The constant's comment says so and names the deferral.

**What I preserves.** Only the number changes:

- The drain loop (`_Supervision`'s `DRAINING` wait), its poll and scan cadences, and what ends
  the wait: the owned set empties (finish), `--timeout` expires (interrupt), or the bound passes
  (detach). `--timeout` exists only on the launch drain: `step`/`run` pass it to
  `execute_step` (`controller/cli.py:901`), so there an operator timeout below 3 hours still
  bounds a drain.
- The `resume` re-attach drain has no timeout, today or after this change. `worker.reattach`
  builds its `_ReattachedSupervision` with `timeout=None` (`controller/worker.py:1676`), so
  `_expired()` (`controller/worker.py:1118`) is never true, and `resume` takes no `--timeout`
  (`controller/cli.py:1092` calls `job.resume(target, identity=..., runtime=...)`). The branch
  for a worker gone with no `ending_offset` drains "with the same bound" and no timeout either
  (`controller/job.py:3242-3244`). So `DRAIN_DETACH_SECONDS` alone bounds a re-attach drain.
- A detach still ends nothing, keeps the record `LAUNCHED` at `DRAINING` with
  `drain_detached_at`, keeps the anchor holding the lock and exits 45. `resume` re-attaches and
  drains again with a fresh bound, now 3 hours.
- The record schema, the events, the exit codes and `controller/GENERATION.json` are unchanged.
  The value is not recorded anywhere, so no stored record depends on it.
- The messages that state the bound read the constant: `observe` renders it as minutes
  (`detached after 180:00`) and `job` as seconds (`after 10800 s`). Neither code path changes.
- Tests that exercise a detach already patch the constant to 1-30 s
  (`tests/test_job.py:2647,2711`, `tests/test_resume.py:3175,3350`,
  `tests/test_worker.py:2573`). They keep testing the same semantics and need no change.

**Cost.** A drain whose owned process never ends (an unrecognised daemon, a hung tool) now holds
the lifecycle lock for up to 3 hours before the operator is told, not 10 minutes. `status`,
`follow` and `inspect` already show a draining job with its pids and elapsed time, so the
operator can end the process sooner.

A second cost falls on `resume`. When an owned process never ends, `workflow-controller resume
<repo>` now waits in the foreground for up to 3 hours, not about 10 minutes, and each further
`resume` waits up to another 3 hours. `--timeout` cannot shorten it (above). The operator stops
sooner in one of two ways. Ending the named pids lets the drain finish at once. Interrupting
`resume` (Ctrl-C) ends only the Controller and nothing it owns (`launch`'s contract, which
`reattach` shares: "A `KeyboardInterrupt` during supervision propagates and ends nothing",
`controller/worker.py:944-945`; `README.md:658`), so the job stays
`LAUNCHED` at `DRAINING` for a later `resume`. CP5C's README edit says both.

This is the trade the operator chose (D12). Giving `resume` a `--timeout` of its own would change
Controller behaviour beyond the constant, so it is not part of this amendment. It is deferred with
the rest of the drain-bound configurability (D13).

### J. The local largest-atom floor (CP6B, amendment 2)

**Problem** (LIR4-001, local implementation review round 4). `shard_count` computes
`effective = max(target, largest estimate)`. Locally, the estimates come from the local profile,
falling back per atom to the committed `tools/test_timings.json`. Until the functional review,
that file held local-machine seconds, so the fallback was harmless. F1 (`9501fbb`) refreshed it
from CI, as §B intended, and the acceptance matrix's committed estimate became 207 s, its CI
time; locally it takes about 81 s. Whenever the local profile has no entry for the matrix, that
CI estimate becomes the local floor:

- **Cold cache** (a fresh clone, or `XDG_CACHE_HOME` cleared): the reviewer measured 5 shards
  and 145.9 s for the full selection, over the 120 s bar. Before F1 the same case planned 8.
- **A local profile without the matrix atom** (an operator who has run only `run_tests.py tests`
  or narrow selections): 4 shards, with estimates of 207/195/195/195 s.
- A warm, complete local profile is unaffected: 8 shards, 86.9 s.

The first run on every clone misses the bar. It also contradicts §B ("mixing profiles distorts
only balance") and D9.

**Change.** For the `local` profile, only atoms whose estimate comes from a local-machine timing
file are floor atoms (§C, "Floor atoms"). `build_plan` knows each timing file's path, so it marks
an atom local-sourced when the first file that records it is not `repo_root /
tools/test_timings.json`, and it passes `shard_count` the floor atoms' estimates alongside all
the estimates. `shard_count` takes the floor from that list, and from all the estimates when it
is not given, which is what the `ci` profile does. The committed estimate still counts in
`total`, so a cold-cache plan still uses the CI profile's relative weights, and LPT still gives
the matrix a shard of its own.

**Evidence** (2026-09-27, at `68ffbbb`, on this machine; a scratch-worktree prototype of exactly
this change). The shard count on the full selection with the committed profile, from
`build_plan` with `profile_parameters("local", cpu_count=n)`, where "partial" is this machine's
local profile minus the matrix atom:

| local profile | CPUs | before J | after J |
| --- | --- | --- | --- |
| none (cold) | 8 / 16 / 4 | 5 / 5 / 4 | **8 / 8 / 4** |
| partial | 8 / 16 / 4 | 4 / 4 / 4 | **8 / 8 / 4** |
| complete | 8 / 16 / 4 | 8 / 8 / 4 | 8 / 8 / 4 (unchanged) |

The CI plan of the CI placement from the committed profile has the same digest before and after
(`786666d77b24...`, 5 shards). Two full runs of the prototype passed:

- cold cache: 8 shards, 2061 tests, 87.8 s, balance 1.08, the matrix alone on shard 0;
- partial profile: 8 shards, 87.3 s, balance 1.08.

**What J preserves.**

- CI planning: the `ci` profile's floor is every atom, as before, so every CI plan and digest is
  unchanged. `timings_for("ci")` reads only the committed file anyway.
- The estimates themselves, `estimate_atoms`, the LPT assignment, the exclusive shard, the plan
  schema and its digest inputs. A local plan's `N` can change, and `N` is an output that the
  digest already covers.
- A warm, complete local profile gives the same count as before, because every atom is then a
  floor atom.
- I1-I4: the floor decides how many shards there are, never which tests run.

**Cost.** Without local data, the local count follows `total / target`, and `total` is in CI
seconds, which are larger than local ones. So a cold cache can plan more shards than a warm one
would, up to `min(8, CPUs)`, for a small selection. `run_tests.py conformance` on a cold cache
plans 6 shards instead of 2, for example. Each extra shard costs one process start-up, and the
CPU cap keeps it at one shard per CPU, the measured-safe default. The next run plans from the
local profile that this run wrote.

## Checkpoints

<!-- generated by workflow_state.render_registry_markdown(registry) -- do not edit by hand -->
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Inventory, atoms and selection: tools/test_shards.py builds one deterministic inventory (controller family equal to unittest discover's ids and order, refusing import failures, _FailedTest and duplicates; conformance family read from the managed workflow-conformance.yml), class atoms with whole-module atoms for setUpModule/tearDownModule modules, canonical order, name-based selection, and the CI placement partition | - | 2 | 1 |
| CP2 | Result records and timing model: per-shard result schema, the committed CI timing profile schema and the untracked local profile, EWMA update/merge/prune policy, estimates for unknown atoms, and corrupt-or-missing timing data falling back to defaults without affecting selection | CP1 | 2 | 1 |
| CP3 | Adaptive deterministic planner: shard count clamp(ceil(total/max(target, largest atom)), min, max) with profile parameters, deterministic LPT assignment preserving canonical order within shards, plan.json with plan_digest, planner self-validation, and the serial/sharded equivalence property tests | CP2 | 2 | 1 |
| CP4 | Executor, local runner and aggregation: tools/run_tests.py (full/selected parallel run, --serial reference, --plan-only, --replay, exec-shard, plan, aggregate, timings merge), per-shard process isolation (session, TMPDIR, XDG_STATE_HOME, pip cache, leak marker, SIGINT reset), loaded-vs-planned id refusal, run-time coverage proof, verdicts and exit codes, failure diagnostics with log paths and reproduction commands, Ctrl-C handling, and the committed timing profile seeded from a measured serial run | CP3 | 3 | 1 |
| CP5 | Serialization registry and timing-flake hardening: the audited EXCLUSIVE_ATOMS mechanism (empty unless evidenced), the stress protocol run under default, 4-CPU and oversubscribed configurations, and each exposed test-fixture race (starting with the DrainDetachJobTest escapee exec race and the intermittent CheckpointPredicateUnitTest error) fixed at its cause without loosening assertions, lengthening windows or retrying | CP4 | 3 | 1 |
| CP5B | Ownership provenance follows the current basis (amendment 0, Design H): _Ownership.scan relabels a still-matching recorded entry's source from the current scan's basis (group, tag, adopted; the recorded fallback keeps it) while pid, start_ticks, cmdline and the seen sample stay first-sighting; _Supervision._signature includes source so a relabel publishes; OwnershipTest reads the last observed ppid and the last DRAINING entry; a gated escapee deterministically reproduces the pre-setsid first sighting; targeted stress clears IR1-003 | CP5 | 2 | 1 |
| CP5C | Drain detach bound 600 -> 10800 s (amendment 1, Design I): controller/worker.py DRAIN_DETACH_SECONDS = 10800 as an interim constant with every other drain semantic unchanged; tests/test_observe.py's hard-coded detach text; README and ADR 0004 state the new bound; docs/ROADMAP.md records that configurability of the drain bound and other Controller tunables is deferred to the next settings/configuration milestone | CP5B | 1 | 1 |
| CP6 | CI integration: tools/ci_workflows.py's validate.yml model gains plan / tests (dynamic fromJSON matrix, digest-checked exec-shard) / tests-result (always-run aggregate and coverage check) jobs, the hand-curated CONTROLLER_SHARDS and separate conformance matrix are removed, package reads the placement, workflows re-rendered, test_ci_workflows updated | CP4 | 2 | 1 |
| CP6B | Local largest-atom floor from local-machine estimates only (amendment 2, Design J): for the local profile, build_plan passes shard_count only the estimates of atoms whose first recording timing file is not the committed tools/test_timings.json, so CI-fallback estimates still count in the total and the assignment but never set the floor; CI planning unchanged; regressions on the real inventory and committed profile that an empty and a matrix-less local profile both plan 8 shards on 8 CPUs; README and ADR 0005 state the rule and correct 'only balance' | CP6 | 1 | 1 |
| CP7 | Documentation, measurement and full verification (terminal checkpoint): README development and CI sections, ADR 0005 adaptive test sharding, docs/ROADMAP.md entry, serial-vs-sharded id comparison, the stress protocol re-run, packaged-runtime suite, ci_workflows --check, the local performance acceptance measurements, and amendment 0's documentation (the first-sighting race closed by Design H, I10, README timing-refresh wording) amendment 1's release-note line for the drain bound, amendment 2's cold-cache measurement (no local profile), and re-verification on the amended head | CP5B, CP5C, CP6B | 2 | 1 |

<!-- CP1 -->
### CP1 -- inventory, atoms and selection

Files: `tools/test_shards.py` (new: inventory, families, atoms, canonical order, selection,
`CI_PLACEMENT`, and the conformance list read from the managed workflow),
`tests/test_test_shards.py` (new).

Tests:
- the controller inventory equals `discover`'s ids, in `discover`'s order, exactly (the I7
  definition);
- the conformance family equals the managed workflow's `run:` lines;
- a synthetic tree with an import error, a `_FailedTest`, a duplicate id, or a module with
  `setUpModule`/`tearDownModule` refuses, or forms a module atom, respectively;
- a synthetic module that raises `SkipTest` at import (`ModuleSkipped`) refuses the inventory
  with a named error (LP-R2-002);
- `test_release_txn` and `test_forge` are module atoms, and every other atom is a class;
- selection by module, class, method, `conformance` and `conformance:<file>`, and a name that
  matches nothing refuses;
- the placement partition of the real inventory.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- result records and timing model

Files: `tools/test_shards.py` (result-record and timing schemas, estimates, update/merge, prune,
corrupt-file fallback), `tests/test_test_shards.py`. There is no committed timing file yet:
until CP4 seeds one, planning uses the defaults, which I3 makes harmless.

Tests:
- estimates for known, unknown-in-known-module, unknown-module and unknown-conformance atoms;
- EWMA merge, test-count reset, pruning, and failed or fixture-skipped atoms not merged;
- a missing, unreadable, schema-invalid, negative or non-finite timing file falls back to the
  defaults with one warning and a valid plan;
- the defaults alone give a valid plan for the real inventory.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- adaptive deterministic planner

Files: `tools/test_shards.py` (shard count, LPT, plan JSON, digest, self-validation),
`tests/test_test_shards.py`.

Tests (the equivalence regression suite):
- a property test over 500 seeded random cases (selection subsets of the real inventory, random
  or corrupt or empty timings, `N` from 1 to 32) checks that the union of the shards equals the
  selection and the shards are pairwise disjoint, with no empty shard and every atom whole;
- the same inputs give the same `plan_digest` twice, and in a fresh interpreter under a
  different `PYTHONHASHSEED`;
- the count formula at each boundary: clamping to minimum and maximum, `N ≤ atoms`, the
  largest-atom floor, and `--shards`;
- LPT balance on the recorded baseline stays within 1.10 × the ideal `total/N` when the largest
  atom is at most `total/N`;
- a tampered plan fails the planner's self-validation.
<!-- /CP3 -->

<!-- CP4 -->
### CP4 -- executor, local runner and aggregation

Files: `tools/test_shards.py` (the `aggregate` function, leak scan), `tools/run_tests.py` (new
CLI: default run, `--serial`, `--plan-only`, `--replay`, `exec-shard`, `plan`, `aggregate`,
`timings merge`), `tools/test_timings.json` (new: seeded through `timings merge` from a
`--serial` run of the full selection on the reference machine, `profile: "seed-local"`),
`tests/test_test_shards.py`, `tests/test_run_tests.py` (new: end-to-end over a
synthetic tests tree with passing, failing, erroring, skipping, crashing, hanging and leaking
atoms, so the real suite is not re-run inside itself).

Tests:
- the executor refuses when the loaded ids differ from the planned ids, including the `_FailedTest`
  import-failure shape;
- the recording rules (LP-R1-001), each over a synthetic module:
  - a `tearDownModule` that asserts and fails: shard `FAIL`, exit 1, `fixture_errors` holds
    `tearDownModule (<module>)`, the summary names it, and coverage is exact;
  - a class whose `setUpClass` raises: `FAIL`, exit 1, every id of that class is reported once as
    `error` naming the fixture, and there is no coverage violation or NOT RUN;
  - a test with one failing `subTest` among passing ones, and a test with only passing subtests:
    each parent id is reported exactly once, as `fail` and `pass` respectively, and no
    `<id> (<params>)` id appears in `tests`;
  - an `expectedFailure` that unexpectedly succeeds: `unexpected_success`, `FAIL`, exit 1;
  - a class whose `setUpClass` raises `SkipTest` (LP-R2-001): `PASS`, exit 0, every id of that
    class is reported once as `skip` naming the holder, `fixture_skips` holds
    `setUpClass (<module>.<Class>)`, there is no NOT RUN or coverage violation, and the atom is
    not merged into the timing cache;
  - a module whose `setUpModule` raises `SkipTest`: the same, per module, with
    `setUpModule (<module>)`;
  - for every synthetic case, the shard verdict equals `unittest`'s `wasSuccessful()` in both
    directions, and a record forced to disagree either way is refused with exit 2;
- `exec-shard` accepts exactly one of `--plan` and the planning inputs with `--expect-digest`,
  refuses both or neither, refuses a digest mismatch, and `aggregate` refuses a result whose
  `plan_digest` differs from its plan's (LP-R2-003);
- aggregation detects a missing, duplicate or unplanned id and a crashed shard, and names the
  NOT RUN ids;
- exit codes 0/1/2/130;
- the summary names the failing test, its shard, its log and both reproduction commands, and
  `--replay --shard` reproduces the same ids in the same order;
- the environment shaping is exact (no `PIP_CACHE_DIR` by default, a per-shard one under
  `--isolated-pip-cache`), and `--serial` gets the same shaping;
- shard children have SIGINT at `SIG_DFL` even when the runner itself was started with SIGINT
  ignored;
- a leaked process is reported and its group killed;
- Ctrl-C reports `INTERRUPTED` and leaves no shard process behind;
- the local timing cache is updated, and never from failed atoms, through an atomic replace (a
  write interrupted before the replace leaves the previous file intact);
- the plan digest is identical for two checkouts at different absolute paths, and the
  `exec-shard` argv has no `--count`;
- the committed `tools/test_timings.json` validates against the schema and names only existing
  atoms.
<!-- /CP4 -->

<!-- CP5 -->
### CP5 -- serialization registry and timing-flake hardening

Files: `tools/test_shards.py` (`EXCLUSIVE_ATOMS`, the exclusive phase and CI placement),
`tests/test_test_shards.py`, and the specific test or fixture files the stress protocol
implicates. Expected: `tests/test_job.py`, and possibly `tests/process_fixtures.py`,
`tests/test_worker.py` and `tests/test_job_validation.py`. No `controller/` file.

Work:
- run the stress protocol (G) with the CP4 runner;
- capture the traceback of every failure, including the baseline's intermittent
  `CheckpointPredicateUnitTest` error;
- fix each exposed race under the hardening policy, then re-run the protocol to zero failures.

Tests: the registry audit (existing atom, non-empty reason); the exclusive phase runs alone and
after the parallel phase; each fixed race gets a deterministic reproduction where one is
possible (for example the escapee is asserted by name only after its cmdline is observed).
The checkpoint notes record each stress round's command, configuration and result.
<!-- /CP5 -->

<!-- CP5B -->
### CP5B -- ownership provenance follows the current basis (amendment 0)

Files:
- `controller/worker.py`: `_Ownership.scan`'s recorded-entry refresh and
  `_Supervision._signature`, and nothing else (Design H);
- `tests/test_worker.py`: `OwnershipTest` and new unit tests;
- `tests/fake_claude.py`: `bash_bg`'s `orphan_gate`.

No other `controller/` file changes, and no other test file is expected to change. If
`tests/test_resume.py`'s `DrainDetachedReattachTest` needs an edit, that is a finding to report,
because H should only make its `source` checks hold more often.

Tests:
- **Unit, over a synthetic `/proc` (`_proc_root` patched).**
  - A recorded `group` entry whose process is now outside the group and carries the tag is
    relabelled `tag`. Its `pid`, `start_ticks` and `cmdline` are unchanged, and the `sample` copy
    taken at first sighting still says `group`.
  - Without the tag but with `ppid ==` the supervisor, it is relabelled `adopted`.
  - Found by nothing but its record, it keeps its recorded `source`.
  - A changed `start_ticks` still drops it: pid reuse, as today.
- **Unit, the signature.** Two `details` that differ only in one entry's `source` have different
  signatures, and `_publish` calls `on_state_change` for the second.
- **Membership is unchanged (I10).** For the same synthetic `/proc` sequence, the owned pid set,
  `outside_group`, `excluded`, `seen_count` and `verifiable` are identical with and without the
  relabel.
- **The gated escapee (Design H).** It is first published as `group`, then as `tag`, with the
  same identity and the first-seen `cmdline`.
- **`OwnershipTest`'s corrected `_escaped`/`_orphan`.** The setsid, reparented and no-subreaper
  tests keep their existing assertions.

Verification:
- **Targeted stress, the IR1-003 reproduction.** 8 parallel copies of `python3 -m unittest
  tests.test_worker.OwnershipTest` under `taskset -c 0`, 48 runs, must give **0 failures**. The
  same reproduction at the CP5 head gave 3 in 24.
- **The latent sites.** The same stress, 24 runs each, over
  `tests.test_resume.DrainDetachedReattachTest` and `tests.test_worker` must also give 0
  failures.
- **The full selection** through `tools/run_tests.py` at defaults, with its id set compared with
  a serial run's.

The checkpoint notes record each stress command, its configuration and its result, and close
the CP5 "Still open" item.
<!-- /CP5B -->

<!-- CP5C -->
### CP5C -- drain detach bound 600 -> 10800 s (amendment 1)

Files:
- `controller/worker.py`: `DRAIN_DETACH_SECONDS = 10800` and its comment (interim bound,
  configurability deferred), and nothing else (Design I);
- `tests/test_observe.py`: the draining text expected after a detach becomes
  `detached after 180:00` (`tests/test_observe.py:740`), the one test that hard-codes the default;
- `README.md`: "The drain bound" says 3 hours (10800 s), not 600 s (`README.md:489`). It also
  says that `--timeout` bounds only the `step`/`run` launch drain: `resume`'s re-attach drain has
  no timeout, so `resume` may wait in the foreground for up to 3 hours per call. To stop sooner,
  end the named pids (the drain then finishes), or interrupt `resume`, which ends nothing and
  leaves the job `LAUNCHED` at `DRAINING` for a later `resume` (Design I, "Cost");
- `docs/adr/0004-worker-lifecycle-ownership.md`: "Drain bound" gives 10800 s and one sentence
  naming amendment 1 of `workflow-controller-adaptive-test-sharding` as the change and the
  deferred configurability (`docs/adr/0004-worker-lifecycle-ownership.md:160`);
- `docs/ROADMAP.md`: the deferral of drain-bound and Controller-tunable configurability, per D13.

No other `controller/` file changes. If any other test turns out to depend on the default value,
that is a finding to report, not something to loosen.

Tests:
- the updated `tests.test_observe` expectation, which also pins the new default through its
  rendered text;
- the classes that patch the bound (`tests.test_job.DrainDetachJobTest`,
  `tests.test_resume.DrainDetachedReattachTest` and `SupervisorLockScopeTest`,
  `tests.test_worker.OwnershipTest`) pass unchanged, showing the semantics are unchanged.

Verification: those four classes, `tests.test_observe`, and the full selection through
`tools/run_tests.py` at defaults. Also one check, not a new test, that `job`'s detach message
states the default: grep `controller/job.py` for the `after {worker.DRAIN_DETACH_SECONDS} s`
format (`controller/job.py:3375`) and confirm with
`python3 -c 'import controller.worker as w; print(w.DRAIN_DETACH_SECONDS)'`, run from the
repository root, that it renders
`after 10800 s`.
<!-- /CP5C -->

<!-- CP6 -->
### CP6 -- CI integration

Files: `tools/ci_workflows.py` (the `validate.yml` model's `plan`/`tests`/`tests-result` jobs;
`package` reads the placement; `CONTROLLER_SHARDS`, `PACKAGE_TEST_MODULES`,
`EXCLUDED_TEST_MODULES` and `CONFORMANCE_SUITES` are removed in favour of `tools/test_shards.py`),
`.github/workflows/validate.yml` (re-rendered by `--write`; `ci.yml` and `main.yml` re-rendered,
unchanged unless the emitter touches them), `tests/test_ci_workflows.py`.

Tests: the job graph, the needs and `if: always()`, the matrix `fromJSON` expression, the
`exec-shard` argv (`--expect-digest`, `--ci-placement`, `--profile ci`, and no `--count` or
`--shards`), the placement
partition, the conformance family equal to the managed workflow, the artifact upload and download
names, and `--check` clean.
<!-- /CP6 -->

<!-- CP6B -->
### CP6B -- local largest-atom floor from local-machine estimates only (amendment 2)

Files:
- `tools/test_shards.py`: `shard_count` takes an optional list of floor estimates (all the
  estimates when it is omitted); `build_plan`, for the `local` profile, passes the estimates of
  the atoms whose first recording timing file is not the committed profile; the module docstring
  and both docstrings state the rule (Design J). Nothing else changes;
- `tests/test_test_shards.py`: the tests below;
- `README.md`: the "Timings" bullet says that a CI-profile fallback estimate never sets the local
  largest-atom floor, and that a stale or missing entry costs wall time (shard count and
  balance), never coverage. The same correction applies to the CI paragraph's "A stale or missing
  entry only costs balance";
- `docs/adr/0005-adaptive-test-sharding.md`: "Timing is advisory" and "Planning" give the floor
  rule and correct "a stale profile costs balance only". One sentence names amendment 2 and
  LIR4-001 as the reason;
- `docs/ACTIVE_MILESTONE.md`: the amendment-2 record.

Tests:
- `shard_count` with a floor list smaller than the largest estimate plans `ceil(total / target)`,
  with the floor list omitted it is unchanged, and an empty floor list makes `effective` the
  target;
- `build_plan` with the `local` profile, a local timing file and a committed one under a temporary
  repository root: an atom recorded only in the committed file does not set the floor, and the
  same atom recorded in the local file does. With the `ci` profile, the same committed atom sets
  the floor;
- the amendment's two regressions, both on the real inventory and the committed
  `tools/test_timings.json`, with `profile_parameters("local", cpu_count=8)` and
  `timings_for("local", ...)` against a temporary `XDG_CACHE_HOME`: (a) no local profile plans
  **8** shards; (b) a local profile that records every committed atom except the acceptance
  matrix, with the committed seconds, plans **8** shards. Both plan fewer than 8 without Design J
  (5 and 5 with these inputs), which is checked once by reverting the `build_plan` hunk;
- the CI plan of the CI placement from the committed profile is still the count the formula
  gives over every atom, with the matrix setting the floor.

Verification: `tests.test_test_shards`, `tests.test_run_tests`, `tests.test_ci_workflows`, the
full selection through `tools/run_tests.py` at defaults, and once with an empty temporary
`XDG_CACHE_HOME` (8 shards, within the 120 s bar), and `tools/ci_workflows.py --check`.
<!-- /CP6B -->

<!-- CP7 -->
### CP7 -- documentation, measurement and full verification (terminal checkpoint)

Files:
- `README.md` ("Development": the runner, selection, `--serial`, `--replay`, the results
  directory and the timing refresh. "Continuous integration": the new jobs, the required check,
  the check names, and why the managed conformance workflow is still the floor);
- `docs/adr/0005-adaptive-test-sharding.md` (new: I1-I9, atoms, the equivalence proof, timing as
  advisory, profiles and parameters with their measured justification, rejected alternatives:
  method-level splitting, a static matrix, pytest-xdist and other third-party runners, and
  automatic retries);
- `docs/ROADMAP.md` (the new entry, and the 1.2 CI section's hand-curated-shards line updated);
- `docs/ACTIVE_MILESTONE.md`.

Amendment 0 changes the following. CP7 was complete before the amendment, so these are
revalidation edits:
- The ADR, the roadmap entry and `docs/ACTIVE_MILESTONE.md` no longer list the `OwnershipTest`
  first-sighting `source` race as open. They say that Design H fixed it (CP5B), and the ADR adds
  I10.
- The roadmap entry names the one Controller behaviour change for the next release's notes: the
  `source` label of an owned process now follows its current basis.
- The external review's optional O1: `README.md`'s timing-refresh wording separates ordinary
  test drift, which needs no refresh, from a removed or renamed class. A removed or renamed class
  leaves a stale atom that `CommittedTimingsTest` rejects until `timings merge` prunes it.
  This is documentation only.

Amendment 1 adds one item: the roadmap entry's release-note line also names the drain detach
bound, 600 s to 10800 s, as an interim value with its configurability deferred (D12).

Amendment 2 adds no documentation of its own to CP7 (CP6B carries it). CP7 re-runs its
verification and measurement on the amended head, and the performance table gains the
cold-cache row. `docs/ACTIVE_MILESTONE.md` records the new measurements.

Verification and measurement:
- the full suite serially, both through `python3 -m unittest discover -s tests -t .` and through
  `run_tests.py --serial`, compared id for id;
- the full selection through `run_tests.py` at defaults, 3 times, recording wall time, per-shard
  walls, the balance ratio and the largest atom;
- the full selection 3 times more, each with a fresh, empty `XDG_CACHE_HOME` (amendment 2);
- the stress protocol (G) again, on the amended head;
- the packaged-runtime suite under `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`;
- `tools/ci_workflows.py --check`;
- the performance acceptance table (below), filled from these runs.
<!-- /CP7 -->

## Performance acceptance

Measured on the reference machine at CP7 and recorded in the CP7 notes and the implementation
bundle's `TEST_RESULTS.md`:

| measure | baseline | acceptance bar |
| --- | --- | --- |
| full selection (controller + conformance), local defaults | ~602 s serial (483 + 119) | median of 3 runs **≤ 120 s** |
| the same, with no local profile (each run with a fresh, empty `XDG_CACHE_HOME`; amendment 2) | 145.9 s, 5 shards, at `b2650d4` (LIR4-001) | median of 3 runs **≤ 120 s**, planned at `min(8, CPUs)` shards |
| controller-only selection (`tests`), local defaults | 483 s serial | median of 3 runs **≤ 90 s** |
| balance ratio, local defaults | n/a | `max/mean` shard wall **≤ 1.25**, or the max shard equals the largest atom's own time ± 10% |
| sharded vs serial id set | n/a | identical, every run (I2) |
| stress protocol (G) | 1 failure at 12-on-4 | **0 failures** |

CI, measured during functional review, needs a pushed branch and a Draft PR, which only the
operator can create:

| measure | baseline | acceptance bar |
| --- | --- | --- |
| `validate` critical path | 3 min 23 s (`36257702439`) | ≤ 5 min, and no more than 45 s slower than the baseline |
| `tests-result` coverage check | n/a | exact, on a real run |
| a deliberately failing test in a throwaway commit | n/a | `tests-result` fails, and its summary names the test, the shard, the log artifact and the reproduction commands |

The 120 s local bar is above the prototype's 64 s Controller time plus the 81 s conformance
atom, which run in parallel. It leaves headroom for the extra processes and the exclusive phase.
If CP7 measures well inside it, the measured number is what gets recorded; the bar itself does
not move.

## Decisions for the reviewer and the user

- **D1 Inventory scope: controller and conformance.** The runner's full selection includes the
  frozen conformance suites, as whole-file atoms run with the managed workflow's command (the
  three stated differences are under Design D, step 5).
  So one local command reproduces what CI validates, and CI plans both families with one
  planner. *Alternative:* controller only, with conformance left in its own CI matrix. That
  leaves two inventories, which the brief asks to avoid.
- **D2 Conformance suites are never subdivided.** They are Workflow Manager-owned and frozen.
  Loading them class by class through `python3 -m unittest` from `scripts/` would run them under
  a different `__name__` than the managed workflow does, which is not provably identical. Running
  each file whole as `__main__`, from `scripts/`, keeps the suite's own code path identical; only
  the interpreter choice and the inherited environment differ (Design D, step 5). The
  cost is a floor: the acceptance matrix takes 81 s locally and 185 s on CI. *Revisit* if
  Workflow Manager ships them as importable unittest modules.
- **D3 Profile-specific targets** (local 60 s, CI 180 s) instead of the suggested uniform 240 s,
  justified under Design C. The operator may prefer different numbers. They are constants, and
  changing them needs no redesign.
- **D4 No method-level splitting.** The largest class is 34 s locally, below both the local
  target's effective floor (81 s) and CI's (185 s), so splitting gains nothing today. It would
  also double `setUpClass` wheel builds and break `test_release_txn`'s module assertion. The
  planner prints a warning when an atom exceeds the effective target, so future growth is
  visible. That is the signal to revisit this.
- **D5 `EXCLUSIVE_ATOMS` starts empty**, and entries need evidence. *Alternative:* pre-register
  the timing-sensitive worker modules as exclusive. That would forfeit most of the local gain,
  because `test_worker` is 32% of the suite, and would hide defects the brief asks to surface.
- **D6 No timeout-multiplier knob.** A global multiplier would change what the timing tests
  assert and could hide regressions. Races are fixed at their cause instead (G).
- **D7 A leaked process is a warning, not a failure**, in this milestone: nothing today asserts
  zero leaks, and the lifecycle-ownership tests intentionally create orphans that
  `reap_recorded_workers` cleans up. *Alternative:* fail on a leak after CP5's triage. Recommended
  as a follow-up once a clean baseline exists.
- **D8 CI placement is unchanged.** `test_integration_disposable_repo`'s three non-gated classes
  still do not run in CI, as today. Running them would add coverage, but it would also run
  `git status` against the checkout. That is a separate decision.
- **D9 The committed timing seed was local-machine data** until the functional review's first CI
  run refreshed it (F1, `9501fbb`). Its provenance never affects which tests run (I3), but it
  can affect the shard count as well as the balance, because the estimates set both `total` and
  the largest-atom floor (§C). Revision 7 corrects this decision's earlier "only balance is
  affected", which LIR4-001 disproved: CI estimates used as the local fallback set a CI-sized
  local floor. Design J (D14) removes that one effect. A committed estimate still counts in a
  local plan's total, so it can still change the local count, but only through `total / target`,
  which the CPU cap bounds.
- **D10 `validate`'s job names change**, so an external branch-protection rule would need
  updating. None is configured, and the README already says branch protection is not required.
- **D11 Fix the first-sighting race now, rather than defer it** (amendment 0). The external
  review's I1 accepted either. Fixing it is recommended because:
  - the change is two expressions in one file, and Design H limits it to provenance (I10);
  - deferring would leave a job record that can say `group` for a process the Controller owns
    only by its tag, although CP5 has already measured that case;
  - it would also leave one test that fails under the stress regime this milestone introduces,
    so a known flake would sit in the suite that the zero-failure bar (G) is meant to keep clean.

  *Alternative:* an amendment that authorizes deferral, keeps the race and its measurements as
  evidence, and leaves `controller/` untouched. The external review names this as acceptable.
  It would drop CP5B and keep the roadmap's "left for later" line. **This is the operator's
  choice.** If they choose deferral, the next revision drops CP5B and Design H.

- **D12 A 3-hour drain bound, as a constant, now** (amendment 1, the operator's decision). The
  operator reproduced legitimate owned work outliving the 600 s bound and chose 10800 s as an
  interim value. It stays a module constant: making the drain bound and the other Controller
  tunables configurable is deferred to the next settings/configuration milestone, so this
  milestone adds no setting, flag or environment variable. The trade has two costs (Design I,
  "Cost"): a never-ending owned process holds the lifecycle lock for up to 3 hours before the
  detach, and `resume`'s re-attach drain, which `--timeout` does not bound, waits in the
  foreground for up to 3 hours per call unless the operator ends the named pids or interrupts it.
  *Alternatives, not taken:* an unbounded
  drain (ADR 0004 rejected it: one unrecognised daemon would hold the lock forever), or a
  configurable bound now (a configuration design this milestone does not have).
- **D13 Where the deferral is recorded.** `docs/ROADMAP.md` has no settings/configuration
  milestone yet. CP5C adds the deferred item to 1.4's follow-up list ("make the drain detach
  bound and the other Controller tunables configurable, including a `--timeout` for `resume`'s
  re-attach drain; 10800 s is an interim constant, amendment 1 of 1.2.1"), and 1.2.1's entry
  points to it. *Alternative:* a new numbered roadmap
  section for Controller settings. The operator may prefer that; it changes only where the line
  sits.

- **D14 Remedy (c) for LIR4-001: only local-machine estimates set the local floor** (amendment
  2, the operator's choice). The review offered three remedies. The operator chose (c), and the
  plan does not reopen that choice. For the record, the other two were modelled on an 8-CPU
  machine before the amendment was requested:
  - (a) scaling the CI profile to local seconds cannot fix the cold case. While the scaled
    largest estimate stays above the target, the count is `ceil(total / largest)`, and a
    uniform scale leaves that ratio unchanged (968 / 207 gives 5);
  - (b) committing a local-machine seed as a second file (the pre-F1 file) also plans 8 in both
    cases. But it adds a second committed profile that needs its own refresh discipline.

  (c) needs no new file and no parameter, and it leaves CI untouched.

`docs/TECHNICAL_DECISIONS.md` does not exist in this repository, so there are no "Open decision"
rows to check. No ADR decision is reversed. ADR 0002 records "the Controller suite in seven named
shards" and ADR 0003 "a `trunk` shard". ADR 0005 supersedes that one operational detail and says
so, and neither earlier ADR is edited.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-adaptive-test-sharding-artifacts.json` starts from
`generate_artifacts_declarations(..., work_item_type="product")` and is fitted to this plan's
footprint, as the previous Controller milestones' declarations were:

- **Plan stage:** protected = this plan, its registry and its mapping. It inherits the
  exclusions, and adds `controller/`, `tests/`, `tools/`, `.workflow-controller/`,
  `pyproject.toml` and `setup.py` as excluded, because they are implementation content.
- **Implementation stage:**
  - protected prefixes: `controller/`, `tests/`, `tools/` (this milestone's main deliverable),
    `.workflow-controller/` and the inherited `docs/adr/`;
  - protected paths: `README.md` (moved from the template's exclusions), `pyproject.toml`,
    `setup.py`, the three rendered `.github/workflows/` files (`validate.yml`, `ci.yml`,
    `main.yml`) and the artifacts file itself;
  - plus the inherited product-template entries the JSON keeps. This milestone edits none of
    them.

  `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md` and `docs/ai-workflow/` stay excluded. They are
  narrative and bookkeeping.

`scripts/`, `.claude/commands/`, `.workflow-manager/` and `workflow-conformance.yml` keep their
inherited classification and are not edited. This milestone only *reads* the managed workflow
file.

## Verification

CP1-CP3 each run the full Controller suite serially (`python3 -m unittest discover -s tests -t
.`, in the foreground: about 8 min, inside the Bash tool's 10-minute limit), plus the
checkpoint's own new tests. From CP4 on, each checkpoint runs the full selection through
`tools/run_tests.py` and compares its id set with the serial run's. A serial run started in the
background must first reset SIGINT to `SIG_DFL` (Design D). CP5 and CP7 run the stress protocol. CP7 adds the
packaged-runtime suite, `tools/ci_workflows.py --check` and the performance measurements.
CP5B runs its targeted `OwnershipTest` stress and the full selection. Every checkpoint that
amendment 0 reconciles to `NEEDS_REVALIDATION` re-runs its own tests and the full selection at
the amended head (see "Revision 4").
CP5C runs the four classes that patch the bound, `tests.test_observe` and the full selection,
and checks that `job`'s detach message states `10800 s` (Design I).
CP6B runs the planner and runner tests, `tests.test_ci_workflows`, `tools/ci_workflows.py
--check`, and the full selection twice: at defaults, and with an empty `XDG_CACHE_HOME`
(Design J).
Nothing needs the real `claude`. Package-index access **is** needed, as it already is at the base
commit: `fixtures.editable_install` runs a build-isolated `pip install -e` that fetches the build
backend, and it is used by `tests.test_identity` (lines 321 and 357), `tests.test_handoff` (529),
`tests.test_cli` (235) and `tests.test_packaged_runtime` (606). With the shared pip cache
(Design D) a warm cache serves these from local files; this milestone does not remove that
dependency.

## Migration / data-integrity notes

- **No stored-data or runtime migration.** The only `controller/` changes are Design H
  (amendment 0) and Design I's constant (amendment 1).
  - Job records keep their schema and their `source` vocabulary, so there is nothing to migrate.
  - A record written by 1.2.0 re-attaches as today (`_recorded_ownership`), and its entries are
    relabelled by the next scan that finds them by group, tag or adoption.
  - The runtime layout is untouched. The wheel's `controller/worker.py` changes for the next
    release only.
- **Developer workflow.** `python3 -m unittest ...` and `cd scripts && python3 <suite>` are
  unchanged (I7). The runner is additive.
- **CI.** The first CI run on the milestone branch used the seeded timing file, and F1
  refreshed it from CI. A missing or stale entry can change the shard count and the balance,
  never which tests run.
- **Local timing cache.** Design J needs no migration. An existing local profile is read as
  before, and a missing one now plans from the target rather than from a CI-sized floor.
- **Artifacts.** Results directories live under `$TMPDIR` and the local timing cache under
  `$XDG_CACHE_HOME`. Nothing new is written into the checkout, so there is no `.gitignore`
  change.
- **Rollback.** Restoring the previous `tools/ci_workflows.py` and re-rendering returns CI to the
  hand-curated matrix. No other state depends on this milestone. Reverting CP5B's `controller/`
  hunk restores first-sighting labels, and no stored record depends on either behaviour.
  Reverting CP5C's constant restores the 600 s bound; the bound is never recorded, so nothing
  stored depends on it either.

## Plan review decisions

### Revision 2 (local-model plan review, round 1, `REVISE`)

All six findings were validated against the repository and accepted. None is rejected.

- **LP-R1-001 (Important), accepted.** A probe module under Python 3.14.7 confirmed each premise:
  a failing `setUpClass` and a failing `tearDownModule` each reach `addError` with a
  `unittest.suite._ErrorHolder` (`setUpClass (pkg.test_a.C)`, `tearDownModule (pkg.test_a)`),
  and the class's tests are never `startTest`ed; subtests arrive only through `addSubTest`, with
  ids `<id> (i=1)`, and a failing one suppresses the parent's `addSuccess`; an unexpected success
  makes `wasSuccessful()` False. The safety `tearDownModule`s are
  `tests/test_release_txn.py:63-67` and `tests/test_forge.py:26-28`; 29 test modules use
  `subTest`. Design B gains `fixture_errors` and the "Recording rules"; Design E's verdicts, exit
  table and summary cover fixture errors and unexpected successes; CP4 gains the four synthetic
  cases the review listed.
- **LP-R1-002 (Important), accepted, first option.** `tests/fixtures.py:88-98` runs a
  build-isolated `pip install -e` with index access, used at `tests/test_identity.py:321,357`,
  `tests/test_handoff.py:529`, `tests/test_cli.py:235` and `tests/test_packaged_runtime.py:606`.
  pip 26.2.1's HTTP cache writes through `adjacent_tmp_file` then `replace`
  (`pip/_internal/network/cache.py:85-91`), so the claimed concurrent-write hazard is not real.
  The default is now the shared cache, `--isolated-pip-cache` is the opt-in, and the
  Verification section now names the index-access requirement instead of denying it.
- **LP-R1-003 (Optional), accepted.** `--count` is dropped from `exec-shard`; the parameters in
  the digest are the planning inputs, and `N` is an output. `timing_source` is
  repository-relative, or `"local-profile"` plus its hash. CP4 and CP6 test both.
- **LP-R1-004 (Optional), accepted.** `validate.yml:47-67`'s `conformance` job has no editable
  install, as stated. Design D step 5 now uses `sys.executable` deliberately and lists the three
  differences from the managed command; D1 and D2 no longer claim exactness.
- **LP-R1-005 (Optional), accepted.** `grep -c "def setUpClass" tests/*.py` gives 10 definitions
  in 8 modules; the Isolation audit is corrected.
- **LP-R1-006 (Optional), accepted.** Timing-file writes are temp-file, `fsync`, `os.replace`;
  CP4 tests that an interrupted write leaves the previous file intact.

No checkpoint was added, removed or renamed, and no requirement changed, so the registry and
mapping are regenerated at revision 2 with the same checkpoints.

### Revision 3 (local-model plan review, round 2, `REVISE`)

All three findings were validated against the repository and accepted. None is rejected.

- **LP-R2-001 (Important), accepted.** A probe module under Python 3.14.7 confirmed the premise:
  a `setUpClass` raising `SkipTest` reaches `addSkip` with an `_ErrorHolder` described
  `setUpClass (pkg.test_a.C)`, a `setUpModule` raising it gives `setUpModule (pkg.test_b)`, none
  of their tests is `startTest`ed, and `wasSuccessful()` is True. The repository sites are
  `tests/test_release_tools.py:80-85,328-333` and `tests/test_packaged_runtime.py:169-178`
  (inherited at `:543`), gated on `tests/fixtures.py:100-127`; `validate.yml` installs
  `setuptools>=70.1` only in the `package` job. Design B gains `fixture_skips` and the
  "Fixture skips" recording rule (backfilled `skip` entries, shard `PASS`); the Consistency rule
  is now two-sided equality with `wasSuccessful()`; fixture-skipped atoms are not merged into
  timings; Design E's summary lists fixture skips; CP2 and CP4 gain the tests the review listed.
  Method-level `SkipTest` (`tests/test_buildinfo.py:355`, `fixtures.require_wheel_build`) already
  reaches `addSkip` on the planned test itself and needs no rule.
- **LP-R2-002 (Optional), accepted, refuse.** The same probe confirmed an import-time `SkipTest`
  loads as `unittest.loader.ModuleSkipped` with the synthetic id
  `unittest.loader.ModuleSkipped.pkg.test_c`. `grep` finds no module-level `raise
  unittest.SkipTest` in `tests/`. Design A and the executor's step 3 refuse it by name, since the
  module's real ids can never be inventoried; CP1 tests the refusal.
- **LP-R2-003 (Optional), accepted.** Design D now defines `exec-shard`'s two mutually exclusive
  plan forms (a recorded `--plan`, or the planning inputs plus `--expect-digest`), states that
  the recompute form writes no `plan.json`, and that `aggregate` reads only the `plan` job's and
  refuses a result with a different `plan_digest`. Design F's `tests-result` downloads that
  file. CP4 tests both forms.

No checkpoint was added, removed or renamed, and no requirement changed, so the registry and
mapping are regenerated at revision 3 with the same checkpoints.

### Revision 4 (plan amendment 0, manual external implementation review I1, `REVISE`)

This revision is not a plan-review round. It answers implementation review I1 through the
plan-amendment mechanism (`amendment_history[0]`, requested at `b8b9a0f`). The review's other
items are handled as follows:

- **IR1-002** is ratified, and nothing changes. Design H keeps `cmdline` first-seen for this
  reason.
- **O1** is folded into CP7, as documentation only.
- **O2** (a `REFUSED` shard's reasons in its own step output) stays an optional follow-up. It is
  not in this amendment's scope.

**What changed:**

- **Scope.** The one `controller/` change is Design H, delivered by the new checkpoint CP5B. The
  Non-goals, the header, the Migration notes and I10 state its limits.
- **Evidence.** The Investigation gains "The ownership first-sighting race", with CP5's
  measurements and the static analysis of `source`'s readers.
- **Decision.** D11: fix the race rather than defer it, with deferral as the operator's
  alternative.
- **Checkpoints.**
  - CP5B is new. It depends on CP5, because it uses CP5's `orphan_pid_file` fixtures and its
    stress protocol.
  - CP7 now depends on CP5B and CP6, not CP5 and CP6, and its name and prose gain the
    amendment's documentation and re-verification.
  - No checkpoint was removed or renamed.
- **Anchors.** Every checkpoint section is now delimited by a `<!-- CPn -->`/`<!-- /CPn -->`
  pair. Each pair encloses the whole section, from the line above its heading to the section's
  last line.
- **Requirements.** The mapping gains R15, Controller ownership provenance, mapped to CP5B and
  CP7. R11 now includes CP5B. R14 is reworded: 1.2.0's released artefact, its packaging scope and
  its release classification are preserved, and Design H is the one runtime change.

**Reconciliation, predicted from the `/approve-review plan` algorithm rather than claimed.**
Revision 3's approved document (blob `9912e35`) has no checkpoint anchors. So every checkpoint
it shares with this revision reconciles to `needs_revalidation`, whatever its content, because
the pre-side hash is `None`. CP5B reconciles as `new`.

| checkpoint | reconciled | revalidation expected |
| --- | --- | --- |
| CP1-CP4, CP6 | `needs_revalidation` (anchor legacy default) | No code change. Re-run the checkpoint's own tests and the full selection at the amended head. |
| CP5 | `needs_revalidation` (anchor legacy default) | No code change. Re-run its tests, and confirm the stress protocol at the amended head. Its notes' "Still open" item is closed by CP5B, not by CP5. |
| CP5B | `new` | Implement Design H (above). |
| CP7 | `needs_revalidation` (row changed) | The documentation edits listed in CP7, the full verification, and the stress protocol re-run. |

Each revalidation goes through the ordinary `/milestone-implement` path. The implementation
review that follows may focus on CP5B, CP7's edits and the freshness of the bundle, as the
external review anticipated.

### Revision 5 (plan amendment 1, operator-requested drain bound)

This revision is not a plan-review round. The operator requested amendment 1
(`amendment_history[1]`, requested at `9946567`, amendment base `ae74df7`) to make the drain
bound change a reviewed part of this milestone rather than an unrelated working-tree edit. Its
recorded reason: legitimate owned background work, reproduced repeatedly in `workflow-manager`,
outlives the worker by more than 10 minutes, so the 600 s bound detaches and interrupts valid
runs; drain semantics are otherwise unchanged, and configurability is deferred to the next
settings/configuration milestone.

The edit itself (`controller/worker.py:411`, 600 -> 10800) has been in the working tree,
uncommitted, since before CP5B was committed. CP5B was committed and verified without it, in a
clean worktree. CP5C commits it.

**What changed:**

- **Scope.** Design I and the new checkpoint CP5C. The header, the Non-goals (including "no
  settings mechanism"), Verification and the Migration notes state its limits.
- **Decisions.** D12 (a 3-hour constant now, configurability deferred) and D13 (where the
  deferral is recorded).
- **Checkpoints.**
  - CP5C is new. It depends on CP5B, because both edit `controller/worker.py` and CP5B is
    complete, so CP5C starts from a committed `worker.py`.
  - CP7 now depends on CP5B, CP5C and CP6. Its name and prose gain amendment 1's release-note
    line.
  - No checkpoint was removed or renamed, and no other checkpoint's section or registry row
    changed.
- **Requirements.** R16 (the interim drain bound) is new, mapped to CP5C and CP7. R14 now names
  both Controller runtime changes.

**Reconciliation, predicted from the `/approve-review plan` algorithm rather than claimed.**
Revision 4's approved document has anchors, so unchanged checkpoints keep their status.

| checkpoint | reconciled | status after approval |
| --- | --- | --- |
| CP1-CP5, CP5B | `retained` (row and anchored section unchanged) | `COMPLETE` |
| CP6 | `retained` | stays `NEEDS_REVALIDATION` from amendment 0 |
| CP5C | `new` | picked up next: implement Design I |
| CP7 | `needs_revalidation` (row changed) | `NEEDS_REVALIDATION`, as it already was |

The implementation order is therefore CP5C, then CP6's revalidation, then CP7.

### Revision 6 (plan amendment 1, local-model plan review, round 5, `REVISE`)

Round 5 reviewed revision 5 (bundle `aa80c9a3`) and returned 0 Blocking, 1 Important and 2
Optional findings. All three are accepted. Every edit stays inside amendment 1: Design I, D12,
D13, CP5C's section, CP7's registry name, the Verification section and these notes.

- **IMP-001, accepted per resolution (a).** Revision 5 said `--timeout` still bounds a drain.
  That holds only for the `step`/`run` launch drain (`controller/cli.py:901`). `worker.reattach`
  passes `timeout=None` (`controller/worker.py:1676`), `resume` takes no timeout
  (`controller/cli.py:1092`), and the no-`ending_offset` branch drains with no timeout either
  (`controller/job.py:3242-3244`). So `DRAIN_DETACH_SECONDS` alone bounds a re-attach drain.
  Design I's "What I preserves" now says this. Its "Cost" and D12 add the foreground-`resume`
  cost: up to 3 hours per `resume`, with no `--timeout` escape. Both name the two ways to stop
  sooner, which were checked in the code. Ending the pids lets the drain finish. Ctrl-C ends
  nothing (`controller/worker.py:945`), and the job stays `LAUNCHED` at `DRAINING`. CP5C's
  `README.md` edit now tells the operator the same. A `resume --timeout` would change Controller
  behaviour beyond the constant, so it is not added here. D13's roadmap line lists it with the
  deferred configurability, as the review suggested.
- **OPT-001, accepted.** CP7's registry name (and so the generated Checkpoints table) gains the
  missing "and" before "amendment 1's release-note line". CP7 already reconciles to
  `needs_revalidation`, so this changes no prediction.
- **OPT-002, accepted.** CP5C's verification, and the Verification section, add one check, not
  a new test: `job`'s detach message renders the default as `after 10800 s`.

No checkpoint was added, removed or re-ordered. No requirement changed, and the registry and
mapping were regenerated at revision 6. Revision 5's reconciliation table still holds, because
the reconciliation compares against revision 4's approved document: CP1-CP5, CP5B and CP6
`retained`, CP5C `new`, CP7 `needs_revalidation`.

### Revision 7 (plan amendment 2, local implementation review round 4, LIR4-001)

This revision is not a plan-review round. The operator requested amendment 2
(`amendment_history[2]`, requested at `68ffbbb`, amendment base `f358409`) after
`/apply-implementation-review` found that LIR4-001 could not be fixed as a bounded fix: every
remedy contradicted approved plan text (§B's fallback, §C's formula and D9), and
`APPLYING_REVIEW_FEEDBACK` has no path to `AMENDING_PLAN`. The operator recovered the item to
`IMPLEMENTING` (`f358409`) and then requested the amendment. The recorded reason names remedy
(c), 8-shard regressions for an empty and for a matrix-less local profile on an 8-CPU machine,
corrections to the design text, §B, §C, D9, the README and the ADR, and no broader planner
redesign.

**What changed:**

- **Scope.** Design J and the new checkpoint CP6B. The header and a new Non-goal state its limit
  (only the local floor changes).
- **Design text.** §B's committed-profile bullet records F1's refresh. Its drift sentence and
  its "Estimates" fallback bullet say what the fallback may and may not affect. §C gains "Floor
  atoms" and a cold-cache row in the parameter table.
- **Decisions.** D9 is corrected, and D14 (remedy (c), with (a) and (b) as modelled
  alternatives) is new.
- **Performance acceptance.** A cold-cache row: median of 3 runs, each with a fresh
  `XDG_CACHE_HOME`, within 120 s.
- **Checkpoints.**
  - CP6B is new. It depends on CP6, the last checkpoint that edits `tools/`, so it starts from
    the current planner, runner and CI model, including the functional review's F1-F3 commits.
  - CP7 now depends on CP5B, CP5C and CP6B (CP6 is reached through CP6B). Its name and section
    gain the cold-cache measurement.
  - No checkpoint was removed or renamed, and no other checkpoint's row or anchored section
    changed. CP3's formula text in its row is still accurate for CI, and "Floor atoms" refines
    it for local planning.
- **Requirements.** R17 (the local floor ignores CI-fallback estimates) is new, mapped to CP6B and
  CP7.
- **Not in this amendment.** The round-4 review's optional findings stay open and outside the
  recorded reason: LIR4-O1 (the replay path hint for the downloaded `test-plan` artifact),
  LIR4-O2 (one over-long README line) and LIR4-O3 (whether the L2 bar's "baseline" is 3 min
  23 s or 4 min 8 s, which the operator settles at functional review).

**Reconciliation, predicted from the `/approve-review plan` algorithm rather than claimed.**
Revision 6's approved document has anchors, so unchanged checkpoints keep their status.

| checkpoint | reconciled | status after approval |
| --- | --- | --- |
| CP1-CP6, CP5B, CP5C | `retained` (row and anchored section unchanged) | `COMPLETE` |
| CP6B | `new` | picked up next: implement Design J |
| CP7 | `needs_revalidation` (row and section changed) | `NEEDS_REVALIDATION` |

The implementation order is therefore CP6B, then CP7.
