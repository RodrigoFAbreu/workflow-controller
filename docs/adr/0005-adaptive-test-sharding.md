# ADR 0005: Adaptive test sharding

Status: accepted. See
`docs/ai-workflow/CONTROLLER_ADAPTIVE_TEST_SHARDING_PLAN.md` for the full
design record (work item `workflow-controller-adaptive-test-sharding`,
`docs/ROADMAP.md` section 1.2.1). This document records the invariants, the
unit of placement, why a sharded run is equivalent to a serial one, the
planning parameters and their measured justification, and the alternatives
rejected. `tools/` and `tests/` are not in the wheel. The work item makes
two bounded changes to `controller/worker.py`, both through plan amendments
and both shipped with the next release, never as a re-release of 1.2.0:
an owned process's `source` label now follows its current basis (amendment
0, I10 below), and the drain detach bound rises from 600 s to 10800 s as an
interim constant (amendment 1; [ADR 0004](0004-worker-lifecycle-ownership.md)).
The version, the exit-code contract of
[ADR 0001](0001-controller-generation-1-architecture.md), the job-record
schema, `controller/GENERATION.json` and the release pipeline's
classification are untouched.

It supersedes one operational detail of two earlier ADRs, without editing
them: [ADR 0002](0002-release-runtime-identity-and-observability.md) runs
"the Controller suite in seven named shards", and
[ADR 0003](0003-trunk-branch-pr-release-orchestration.md) adds "a `trunk`
shard". The hand-curated shard list (`CONTROLLER_SHARDS` in
`tools/ci_workflows.py`) is gone. CI's matrix is now planned.

## Context

Measured at the base commit (`405f050`, 2026-09-26) on the reference
machine (16 CPUs, Python 3.14.7):

- the Controller suite took **483 s** serially, 1887 tests; the seven
  frozen conformance suites took **119 s** (the acceptance matrix alone
  81 s). A local run of everything CI validates took about **602 s**;
- the time is waiting, not CPU: the long tests wait for real supervision
  windows against the fake worker. `test_worker` alone was 32%;
- CI's eight hand-curated shards held 665 s of test time but ranged from
  3 s to 193 s, a 64x imbalance that someone had to re-curate whenever
  tests were added. `validate`'s critical path was 3 min 23 s.

A throwaway prototype of class-level, longest-first shards ran the
Controller suite in 64 s at 8 shards, and in 68 s at 8 shards on 4 CPUs:
the suite is wait-bound, so parallelism pays even on few CPUs. The
prototype also exposed two hazards the design must close: a shard process
that cannot import `tests` makes `unittest` silently substitute a
`_FailedTest` for each name and "run" it, and oversubscription (12 shards
on 4 CPUs) exposed a test-fixture timing race.

## Decisions

### Invariants

- **I1 Equivalence.** For any selection and any shard count, the plan's
  shards partition the selection: union equal, pairwise disjoint, nothing
  lost or added, for every possible timing input.
- **I2 Proof at run time.** Every shard reports the exact ids it ran. A run
  is `PASS` only if the multiset of reported ids equals the selection. A
  missing, extra, duplicated or substituted id fails the whole run.
- **I3 Timing is advisory.** Timing data changes where a test runs and how
  many shards there are, never whether it runs.
- **I4 Determinism.** The same content, selection, profile, parameters and
  timing file give byte-identical plans with the same `plan_digest`. CI
  shard jobs recompute the plan and refuse to run on a digest mismatch.
- **I5 Serial reference.** `--serial` runs the same selection in canonical
  order through the same executor and environment shaping, in one process.
- **I6 No hidden retries.** A failed test is reported failed and never
  re-run.
- **I7 Existing entry points keep working.** `python3 -m unittest ...` and
  `cd scripts && python3 <suite>` are unchanged; the runner is additive.
- **I8 Isolation.** Shards are separate processes, each in its own session,
  with its own `TMPDIR` and `XDG_STATE_HOME` and an environment marker that
  identifies its descendants. Never threads: several pieces of process-global
  state (the worker's subreaper refcount, cached identity) are per process.
- **I9 Explicit serialization.** A test runs alone only if a reviewed
  `EXCLUSIVE_ATOMS` entry, with a reason, says so.
- **I10 Provenance, not membership** (amendment 0). The Controller fix for
  the ownership race below changes only the `source` label of a process that
  is already owned, and when that label is published. It never changes which
  processes are owned, or anything decided from the owned set.

### One inventory, two families

The inventory is what `unittest.TestLoader().discover("tests", top_level_dir=<repo>)`
loads, identified by `TestCase.id()`, plus one `conformance:<file>` id per
frozen suite, read from the managed `workflow-conformance.yml`. Any discovery
error, substituted test (`_FailedTest`, `ModuleImportFailure`, `_ErrorHolder`,
`ModuleSkipped`) or duplicate id refuses the whole inventory rather than
being planned around. Local runs and CI use the same inventory; CI's
`CI_PLACEMENT` only moves `tests.test_packaged_runtime` to the `package` job
and keeps `tests.test_integration_disposable_repo` (live `claude`, real
spend) out, exactly as before. A test pins that the placement partitions the
inventory.

### Atoms: a class is the smallest unit

An atom is never split. It is a `TestCase` class; a whole module when that
module defines `setUpModule` or `tearDownModule` (detected from the loaded
module, today `test_release_txn` and `test_forge`, whose module teardowns are
safety assertions over the whole module); or a whole conformance suite file.
So class and module fixtures run exactly once per run, as they do serially.
Conformance suites run as `sys.executable <file>` from `scripts/`, the
managed workflow's own command, because they are Workflow Manager-owned and
loading them as modules would run them under a different `__name__`.

### Why a sharded run equals a serial one

- **By construction.** Canonical order is `discover`'s order, followed by the
  conformance suites in managed-file order. The planner assigns whole atoms,
  then restores canonical order within each shard, so each shard is an
  order-preserving subsequence of the serial run and the shards partition the
  selection. The planner re-validates the partition on every plan it builds,
  behind property tests over random selections, shard counts and timing
  inputs, corrupt ones included.
- **At run time.** Before running anything, the executor loads its atoms by
  name and refuses (exit 2) unless the loaded ids equal the planned ids,
  exactly and in order. This closes the prototype's `_FailedTest` hazard.
  While running, a recording result keys every `unittest` event to a planned
  id or to a fixture: fixture errors and fixture skips are recorded under
  their `_ErrorHolder` description and back-filled onto the ids they kept
  from running; subtests fold into their parent; an event the rules cannot
  map refuses the shard. The shard's verdict must equal the in-process
  `unittest` result's own `wasSuccessful()`, in both directions, or the
  shard is refused. The aggregate then compares the reported ids with the
  selection as multisets and names every NOT RUN, duplicate or unplanned id.
- **Measured.** At CP4 and CP7 the sharded run reported exactly the serial
  run's ids, and the serial run's controller ids equalled `unittest
  discover`'s, in order.

### Timing is advisory

Two files share one schema: the committed `tools/test_timings.json` (the
only input CI planning reads, changed only by the explicit, reviewed
`run_tests.py timings merge`), and an untracked local profile under
`$XDG_CACHE_HOME`, updated after every local run. Updates are an EWMA
(weight 0.5); an atom whose test count changed takes the observed value; an
atom that failed, was interrupted or skipped a fixture is not merged. An
unknown atom is estimated from its module's mean seconds per test, else the
profile's, else 0.25 s per test; a conformance suite without history is 30
s. A missing, unreadable or invalid file falls back to defaults with a
warning. Nothing reads timing data to include, exclude or condition a test,
so a stale profile costs balance only. Every write is temporary file,
`fsync`, `os.replace`.

### Planning

```
effective = max(target_shard_seconds, largest atom estimate)
N         = min(clamp(ceil(total / effective), min_shards, max_shards), atoms)
```

Assignment is deterministic LPT: atoms sorted by (-estimate, canonical
index), each placed on the shard with the least (load, index), estimates in
integer milliseconds so every platform compares identically. `plan.json`
records the selection, the parameters, the shards, the timing sources (by
repository-relative path or `local-profile`, plus SHA-256, never an absolute
path) and the SHA-256 `plan_digest` of all of it.

| profile | target | min | max | justification |
| --- | --- | --- | --- | --- |
| `local` | 60 s | 2 | `min(8, CPUs)` | a 240 s target would plan 3 shards (about 200 s); the largest atom (81 s) is the floor anyway; one shard per CPU is the measured-safe default, since 12 shards on 4 CPUs exposed a race where 8 on 4 did not |
| `ci` | 180 s | 2 | 16 | the conformance acceptance matrix is about 185 s on CI, so a smaller target adds billed jobs without shortening the path; 240 s would plan 5 shards of about 200 s, slower than the old matrix |

The values are named constants, overridable per run (`--target-seconds`,
`--min-shards`, `--max-shards`, `--shards`). There is no environment
variable or configuration file.

### Execution and aggregation

Each shard runs `run_tests.py exec-shard` in its own session with stdin at
`/dev/null`, `SIGINT` reset to `SIG_DFL` (a shell's `&` leaves it ignored,
which breaks the Ctrl-C tests), `sys.path[0]` at the repository root, and
`PYTHONPATH` never set. It writes `shard-<i>.json` and `shard-<i>.log`. The
aggregate classifies each shard `PASS`, `FAIL`, `CRASHED`, `REFUSED` or
`INTERRUPTED`, exits 0, 1, 2 or 130, and writes a summary naming each
failing test with its traceback, its log and two reproduction commands (the
isolated `python3 -m unittest <id>`, and `--replay <plan> --shard <i>` for
the exact co-resident order). A process still carrying a shard's marker after
the shard ends is reported and its group killed, as a warning (D7). The
marker nests per executor, so a run started by a test inside a shard never
mistakes its outer executor for a leak.

### CI

`validate.yml` has four jobs: `plan` (plans the CI selection, outputs the
shard indexes, count and digest, uploads `plan.json`), `tests` (a
`fromJSON` matrix, one job per shard, each recomputing and digest-checking
the plan), `tests-result` (`if: always()`: aggregates, fails on a missing plan
or a missing shard result, never passes vacuously, uploads `timings-ci`) and
`package`. `tests-result` is the check that stands for the Controller and
conformance tests, whatever the shard count. The managed
`workflow-conformance.yml` still runs the suites serially in about 5
minutes, so it, not `validate`, bounds when all of a pull request's checks
finish. The CI gain is therefore not the point: the matrix is computed
rather than curated, stays balanced as tests are added, and proves coverage.

### Serialization and timing races

`EXCLUSIVE_ATOMS` is empty. An entry would run alone, locally after every
parallel shard has finished, in CI together on one extra final shard. An
entry needs evidence that the race cannot be fixed in the test (D5):
pre-registering the timing-sensitive worker modules would forfeit most of
the local gain and hide the defects this milestone was asked to surface.
Races that parallel load exposed are fixed at their cause, never by
loosening an assertion, lengthening a window or retrying. Two have been
found, both first-sighting races:

- `DrainDetachJobTest`'s escapee was an assertion that assumed the
  Controller's first sighting of a process came after its `exec`. The
  Controller records an owned process's command line when it first sees
  it, so the test now identifies the escapee by pid (a test defect).
- `OwnershipTest`'s setsid escapee was sometimes reported with `source`
  `group` instead of `tag`, because the Controller also kept the `source`
  of its first sighting, taken before the escapee's `setsid`. That was a
  Controller defect, so it was fixed in the Controller (amendment 0): a
  still-matching recorded entry takes the current scan's basis, and a
  relabel is published. `pid`, `start_ticks`, the command line and the
  `owned_processes_seen` sample stay first-sighting (I10). A test with a
  FIFO-gated escapee reproduces the pre-`setsid` sighting deterministically.

## Rejected alternatives

- **Method-level splitting.** The largest class is 34 s locally, below both
  effective floors (81 s local, 185 s CI), so splitting gains nothing today.
  It would run `setUpClass` wheel builds twice and change the meaning of
  `test_release_txn`'s module-wide safety assertion. `--plan-only` names every
  atom longer than the target; that is the signal to revisit.
- **A static matrix** (the previous design). Manual curation drifted to a 64x
  imbalance, and nothing proved that every module was placed exactly once
  except a separate test over the list.
- **pytest-xdist and other third-party runners.** A new dependency category
  for a stdlib-only project, a different collection and id model from the
  `unittest discover` the developers run, per-test rather than per-class
  distribution (breaking class fixtures and the module safety assertions),
  and no run-time proof that the executed ids equal the selection.
- **Automatic retries.** A retry turns exactly the timing defects this work
  surfaces into green runs. A failure is reported, with its reproduction
  commands, and fixed.
- **Test tiers** (fast/standard/full). The full suite stays the full suite;
  tiers would weaken what "verified" means.
- **A timeout-multiplier knob** (D6). It changes what the timing tests assert
  and can hide regressions.
- **Threads instead of processes.** Process-global state is per process, and
  signal and session handling need separate processes.

## Consequences

- One local command reproduces what CI validates, in about a sixth of the
  serial time.
- Adding a test needs no CI edit. A new class is planned from estimates,
  and a class whose test count changed is scaled per test, until the
  committed profile is refreshed; neither needs a refresh. A removed or
  renamed class leaves a stale atom that `CommittedTimingsTest` rejects
  until `timings merge` prunes it.
- `validate`'s check names changed; an external branch-protection rule would
  need to require `validate / tests-result` and `validate / package`.
- Open: failing a run on a leaked process (D7), and refreshing the
  committed profile from real CI data (it is local-machine data until then,
  D9).
