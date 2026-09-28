# Development and the test runner

[Back to the documentation map](../README.md)

## Working from a checkout

```bash
pip install -e .
python3 tools/run_tests.py                          # everything, in parallel shards (about 1.5 min)
python3 -m unittest discover -s tests -t .          # the Controller's own suite, serially (about 8 min)
CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime -v  # wheel build + venv install
CONTROLLER_LIVE_WORKER=1 python3 -m unittest tests.test_integration_disposable_repo -v  # opt-in: live claude, real spend
python -m pip wheel --no-deps -w dist .             # a local wheel (build_origin "local")
```

The packaging tests skip when a build prerequisite is missing, unless
`CONTROLLER_REQUIRE_PACKAGING_TESTS=1` makes that a failure. A local wheel
build refuses a stale `build/` directory left by an earlier build that
held files this one does not; delete `build/` and rebuild.

Do not set `PYTHONPATH=.`: `tests.test_identity`'s decoy-package test
then imports its decoy and fails. Some tests need package-index access,
because `fixtures.editable_install` runs a build-isolated `pip install -e`.
Run the suite in the foreground: a job backgrounded with a plain `&`
ignores SIGINT, and the Ctrl-C tests then fail spuriously.

## Verification policy

- Full-suite verification uses the sharded default,
  `python3 tools/run_tests.py`. Its coverage check proves every planned
  test ran exactly once.
- A serial run (`--serial`, `--jobs 1`, `--shards 1`) is reference
  evidence for an exceptional question, such as whether a failure depends
  on co-residency. It is not a routine confidence rerun.
- Evidence that already exists is reused while its identity holds: the
  same implementation revision and the same test inventory. Rerun only
  when something relevant changed, never because a longer run feels more
  thorough.
- CI and local timing evidence is reusable on the same terms.

## The test runner

`tools/run_tests.py` (with its library `tools/test_shards.py`, both
stdlib only and outside the wheel) runs a selection of tests in
duration-balanced parallel shards. `python3 -m unittest ...` and
`cd scripts && python3 <suite>` keep working unchanged; the runner is
additive. The design record is
[ADR 0005](../adr/0005-adaptive-test-sharding.md).

```bash
python3 tools/run_tests.py                                  # the full selection
python3 tools/run_tests.py tests.test_worker tests.test_cli.RunRecordCtrlCTest conformance:workflow_state_test.py
python3 tools/run_tests.py --serial [names]                 # the same selection in one process, in order
python3 tools/run_tests.py --plan-only [names]              # print the plan and exit
python3 tools/run_tests.py --replay RESULTS/plan.json [--shard i]   # re-run a recorded plan, or one shard of it
```

- **Selection.** With no names the selection is every test
  `python3 -m unittest discover -s tests -t .` loads plus the seven frozen
  Workflow conformance suites. A name is a unittest dotted name (`tests`,
  a module, a class or a test), `conformance` (every suite) or
  `conformance:<file>`. A name that matches nothing is refused.
- **Shards.** A class is the smallest unit of placement (a whole module
  when it defines `setUpModule`/`tearDownModule`, a whole file for a
  conformance suite), so class fixtures run once, as serially. Each
  shard is a separate process in its own session, with its own `TMPDIR`
  and `XDG_STATE_HOME`. The shard count is planned from recorded
  durations: about 60 s of work per shard, at least 2, at most
  `min(8, CPUs)`. `--shards N` pins it, `--jobs J` runs at most `J` at
  once, and `--target-seconds`, `--min-shards` and `--max-shards`
  override the planning parameters.
- **Proof.** A run passes only if every planned test ran exactly once and
  passed; a missing, extra, duplicated or substituted test id fails the
  run and is named. The summary states the check's verdict either way
  (`coverage: exact; ...` or `coverage: NOT exact; ...`, with the
  missing, unplanned and duplicate counts). A failed test is never
  retried. The exit status is 0 (pass), 1 (a test or fixture failed), 2 (refused, crashed, not run,
  or a coverage violation) or 130 (interrupted: Ctrl-C stops every
  shard's process group).
- **Results.** Each run writes `plan.json`, one `shard-<i>.json` and
  `shard-<i>.log` per shard, and `SUMMARY.md` under
  `$TMPDIR/workflow-controller-tests/<run_id>/` (or `--results-dir`). The
  summary names every failing test with its traceback, its shard's log,
  and two reproduction commands: `python3 -m unittest <id>` alone, and
  `--replay ... --shard <i>` for the exact co-resident order. A process
  that outlives its shard is reported, then killed, as a warning.
- **Timings.** Durations only decide where a test runs, never whether it
  runs. Local runs plan from the untracked
  `$XDG_CACHE_HOME/workflow-controller-tests/timings-local.json`
  (updated after every run), falling back to the committed
  `tools/test_timings.json`, which is all CI plans from. A missing or
  corrupt file falls back to defaults with a warning. A committed (CI)
  estimate used as the local fallback counts in the total and the
  assignment, but never sets the local largest-atom floor: CI seconds are
  not local seconds, so only local-machine estimates can hold the local
  shard count down. The committed
  profile changes only through an explicit, reviewed refresh, for
  example from a CI run's `timings-ci` artifact:
  `python3 tools/run_tests.py timings merge --into tools/test_timings.json DIR...`.
  Ordinary drift needs no refresh and costs wall time (the shard count
  and the balance), never coverage: a class
  whose test count changed is scaled per test, and a new class is
  estimated from its module's mean. A removed or renamed class is
  different: its old entry names an atom that no longer exists, which
  `CommittedTimingsTest` rejects until a `timings merge` prunes it (the
  merge drops every entry the inventory no longer contains).
- **Serialization.** A test runs alone only if `EXCLUSIVE_ATOMS` in
  `tools/test_shards.py` lists its class, with a reason. It is empty;
  an entry needs evidence that the race cannot be fixed in the test.

## Workflow release trees

The Controller's tests never read this repository's own installed
Workflow. Every Workflow release the Controller admits
(`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES`) is vendored under
`tests/workflow_releases/<release>/`: the seventeen `.claude/commands/*.md`
files, `scripts/workflow_state.py`, `scripts/workflow_fingerprint.py` and
`scripts/prepare-ai-review.sh`, at their target paths and modes. Each tree's
`RELEASE.json` records the Workflow Manager commit it was taken from and each
file's sha256 and executable flag, equal to the Manager's manifest. The
Workflow-derived inventories (the phase set, the command partition, the
user-only set, property 5), the decision goldens, the query tests and the
migration tests all run against these trees, once per admitted release.
This repository's installed tree is only checked for equality with the
vendored tree of the release it declares, so updating it through Workflow
Manager needs no code change.

Only `tools/workflow_releases.py` (stdlib only) writes these trees:

```bash
python3 tools/workflow_releases.py check     # every tree against its RELEASE.json; exit 1 names each problem
python3 tools/workflow_releases.py sync 2.6.0 --from ../workflow-manager/distribution/workflow
```

`check` also reports a file the record does not name and an admitted
release with no tree. `sync` copies the subset from a Workflow Manager
checkout's `distribution/workflow` directory and refuses when that
release's directory differs from the recorded commit
(`--manager-commit`, default `HEAD`) or a file's digest differs from the
manifest. With a local Workflow Manager checkout (a sibling
`../workflow-manager`, or `WORKFLOW_MANAGER_DISTRIBUTION` naming its
`distribution/workflow` directory), `tests.test_workflow_releases` also
compares each record with the Manager's manifest; CI has no checkout and
skips that class.

The decision goldens have one file per admitted release. Their generators
take `--release` (default `2.5.1`, the reference release), and `--check`
compares without writing:

```bash
python3 tests/golden/generate_plan_stage_decisions.py --release 2.6.0 --check
python3 tests/golden/generate_external_implementation_review_decisions.py --release 2.6.0 --check
python3 tests/golden/generate_no_policy_lifecycle.py --check                 # 2.5.1 only
```

Without `--check` a generator rewrites its golden, so only run it that way
to change the golden on purpose. The 2.5.1 plan-stage generator's `--check`
reports one known difference (the `AMENDING_PLAN` decline reason), which
`tests.test_golden_plan_stage_decisions` reverts as its one permitted
difference before comparing; that test, not the bare `--check`, is the
check for that file.

**Admitting a future Workflow release** is a deliberate act in a reviewed
plan, never a version-string edit:

1. vendor it: `python3 tools/workflow_releases.py sync <release> --from ...`;
2. measure what changed against the previous release: the phase set, the
   command files and the user-only set, the state writers each automatic
   command declares (property 5), and anything the Controller reads from
   Workflow's files or asks Workflow's queries. For a release that runs
   queries, list the Git commands each query can run: the query's Git
   isolation (`workflow_contract._git_isolation`) relies on 2.6.0's queries
   never asking Git for a patch, a log, a checkout or a fetch, and on
   `post-index-change` being the only hook they fire
   (`workflow_contract._QUERY_HOOK_EVENTS`, which a hook the query fires
   refuses). `tests.test_workflow_contract.GitIsolationTest` pins both for
   2.6.0: it installs every hook githooks(5) names and runs the release's
   queries in place;
3. give it a `controller.workflow_contract.RELEASE_CONTRACTS` entry, with
   the query scripts' sha256 from the Manager manifest when it runs queries,
   and per-release writer declarations in `job.expected_outcomes_for` where
   its commands write differently;
4. add it to `VALIDATED_WORKFLOW_RELEASES`, generate its decision goldens,
   and run the whole suite, which runs every per-release inventory for it;
5. prove the migration from the previous release
   (`tests/test_workflow_release_migration.py` is the model).

The record of the 2.6.0 admission is
[ADR 0006](../adr/0006-workflow-release-admission-and-per-release-contracts.md).

## Design records

Every design decision behind the Controller is recorded either in an ADR or in
the reviewed plan of the milestone that made it. The
[documentation map](../README.md#design-decisions) lists them all.
