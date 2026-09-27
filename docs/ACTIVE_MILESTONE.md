# Active Milestone

## Milestone

`workflow-controller-adaptive-test-sharding`: one test inventory, duration-balanced shards, local
and CI. The operator requested it directly, ahead of `docs/ROADMAP.md` section 1.4, which stays
the next roadmap item.

- Plan: `docs/ai-workflow/CONTROLLER_ADAPTIVE_TEST_SHARDING_PLAN.md`, revision 4 (amendment 0),
  approved at `8b0f522` (`EXTERNAL_APPROVE`). Revision 3 was approved at `5fdea5a`.
- Registry: `docs/ai-workflow/registry/workflow-controller-adaptive-test-sharding-registry.json`
  (CP1-CP5, CP5B, CP6, CP7).
- Amendment 0 reconciliation marked CP1-CP7 `NEEDS_REVALIDATION` (revision 3 had no checkpoint
  anchors) and added CP5B. Each is revalidated through `/milestone-implement`.
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
| CP1 -- inventory, atoms and selection | complete, revalidated at revision 4 | `302d7fc`, revalidation: this checkpoint's commit |
| CP2 -- result records and timing model | complete, revalidated at revision 4 | `4467a80`, revalidation: this checkpoint's commit |
| CP3 -- adaptive deterministic planner | complete, revalidated at revision 4 | `b0eb707`, revalidation: this checkpoint's commit |
| CP4 -- executor, local runner and aggregation | complete, revalidated at revision 4 | `55c855b`, revalidation: this checkpoint's commit |
| CP5 -- serialization registry and timing-flake hardening | complete | `311078f` |
| CP6 -- CI integration | complete | `14bbdcc` |
| CP7 -- documentation, measurement and full verification | complete | this checkpoint's commit |

### CP7 -- documentation, measurement and full verification (complete)

- **Documentation:**
  - `README.md` "Development": the runner (selection, shards, `--serial`, `--plan-only`,
    `--replay`, `--jobs`, planning overrides), the run-time proof and exit codes, the results
    directory and its reproduction commands, the timing profiles and the explicit
    `timings merge` refresh, `EXCLUSIVE_ATOMS`, and the `PYTHONPATH=.` and package-index notes.
    "Continuous integration" now says why the managed `workflow-conformance.yml` (about 5 min,
    serial) is still the floor on a pull request's checks, the `ci` profile's parameters, the
    digest check, and what a failing `tests-result` summary names. The closing pointer list
    gains ADR 0005.
  - `docs/adr/0005-adaptive-test-sharding.md` (new): I1-I9, the inventory and its families,
    atoms, why a sharded run equals a serial one (by construction, at run time, measured),
    timing as advisory, the planning formula and both profiles' parameters with their measured
    justification, execution and aggregation, CI, the serialization registry and the race
    policy, and the rejected alternatives (method-level splitting, a static matrix,
    pytest-xdist and other third-party runners, automatic retries, tiers, a timeout
    multiplier, threads). It states that it supersedes ADR 0002's "seven named shards" and
    ADR 0003's `trunk` shard, without editing either.
  - `docs/ROADMAP.md`: new section 1.2.1 (status "implemented, in review"; 1.4 stays the next
    roadmap item), and 1.2's parallel-matrix line notes the planned matrix.
- **Verification and measurement** (reference machine, 16 CPUs, Python 3.14.7; every run
  under a reaping subreaper, because this session is a Controller worker; runs sequential):
  - `python3 -m unittest discover -s tests -t .`: 2040 tests in 480.8 s, OK (8 skipped).
  - `python3 tools/run_tests.py --serial`: 2047 tests (2040 + 7 conformance), PASS, 606.0 s.
    Its 2040 controller ids equal `discover`'s, **in the same order**; no duplicates.
  - Full selection at defaults, 3 runs: 8 shards each, PASS, walls 83.8 / 83.4 / 83.3 s,
    balance 1.07 each, largest atom `conformance:workflow_acceptance_matrix_test.py`
    (83.1-83.6 s), which is the critical path (the shard holding it alone is the longest).
  - Controller-only selection (`tests`) at defaults, 3 runs: 8 shards, PASS, walls 64.0 /
    62.8 / 63.2 s, balance 1.02 / 1.01 / 1.02, largest atom `tests.test_worker.OwnershipTest`
    (34.1 s).
  - Id comparison: in all six runs the reported ids equal the serial run's (2047, or the 2040
    controller ids), with no duplicates.
  - Stress protocol (G), 13 runs, **all PASS, 0 failures, 0 leaks**, `EXCLUSIVE_ATOMS: 0`:

    | configuration | runs | walls |
    | --- | --- | --- |
    | default (8 shards, 16 CPUs) | 5 | 82.9 / 83.2 / 83.4 / 83.7 / 83.3 s |
    | 4 CPUs (`taskset -c 0-3`, 8 shards) | 5 | 96.8 / 95.9 / 95.3 / 95.4 / 95.2 s |
    | oversubscribed (`--shards 12 --jobs 12`, 4 CPUs) | 3 | 107.4 / 108.1 / 108.7 s |
  - `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime -v`:
    9 tests in 25.7 s, OK (none skipped).
  - `python3 tools/ci_workflows.py --check`: clean.
  - `python3 -m unittest tests.test_plan_document_consistency tests.test_ci_workflows` after the
    documentation edits: 99 tests, OK.
- **Performance acceptance:**

  | measure | baseline | bar | measured |
  | --- | --- | --- | --- |
  | full selection, local defaults | ~602 s serial | median ≤ 120 s | **83.4 s** (median of 3; 7.3x vs the 606 s `--serial`) |
  | controller-only, local defaults | 483 s serial | median ≤ 90 s | **63.2 s** (median of 3; 7.6x vs 480.8 s `discover`) |
  | balance ratio | n/a | ≤ 1.25, or max shard = largest atom ± 10% | 1.07 full (max shard = the 83 s largest atom), ≤ 1.02 controller-only |
  | sharded vs serial id set | n/a | identical every run | identical, 6 of 6 (and 13 of 13 stress runs PASS with exact coverage) |
  | stress protocol | 1 failure at 12-on-4 | 0 failures | 0 of 13 |

  The CI measures (critical path, a real `tests-result` coverage check, a deliberately failing
  test) need a pushed branch and a Draft PR, which only the operator can create; they belong to
  functional review, as planned. Also not done here: refreshing `tools/test_timings.json` from
  real CI data (D9).
- **Open, carried forward:** `OwnershipTest`'s first-sighting `source` race (CP5; seen only at
  8 copies on one CPU, never in any protocol run), which needs a Controller or plan decision; D7
  (failing on leaks) as a follow-up.

### CP6 -- CI integration (complete)

- **`tools/ci_workflows.py`**: `validate.yml`'s model now has four jobs:
  - `plan`: `run_tests.py plan --profile ci --ci-placement --output plan.json --github-output`
    after `pip install -e .`. Its outputs are `shards`, `count` and `digest`, and it uploads
    `plan.json` as the `test-plan` artifact;
  - `tests`: needs `plan`. The matrix is `shard: ${{ fromJSON(needs.plan.outputs.shards) }}`,
    with `fail-fast: false`. Each job runs `exec-shard --profile ci --ci-placement --shard <i>
    --expect-digest <plan digest> --results-dir results`, with no `--count` and no `--shards`,
    and uploads `results-<i>` under `if: always()`;
  - `tests-result`: needs `[plan, tests]`, with `if: always()`. It downloads `test-plan` and
    `results-*` (`merge-multiple`) into `results/` and runs `aggregate`. It uploads
    `plan.json` and `shard-*.json` as `timings-ci` under `if: always()`, which is
    `timings merge`'s input;
  - `package`: unchanged, except that it runs the modules `CI_PLACEMENT` puts in `package`
    (`PACKAGE_TEST_MODULES` is now derived from it).

  `CONTROLLER_SHARDS`, `EXCLUDED_TEST_MODULES` and `CONFORMANCE_SUITES` are deleted, and so are
  the old `controller` and `conformance` jobs. The file loads `tools/test_shards.py` by path,
  so it works both as a script and when a test loads it. `validate.yml` uses major-tag
  actions, per ADR 0002, including `upload-artifact@v4` and `download-artifact@v4`.
  `validate.yml` was re-rendered; `ci.yml` and `main.yml` are byte-identical.
- **Never vacuous.** Both downloads have `continue-on-error: true`, so a missing artifact
  still reaches `aggregate`, which then fails the job and names what was missing:
  - a missing `plan.json` (the `plan` job failed or was cancelled) now refuses, exit 2, with
    "there is no plan at ... nothing ran and nothing can pass";
  - a missing shard result is CRASHED ("no result record"), exit 2.
- **`tools/run_tests.py`**: `plan --github-output` appends `shards=[0,...]` (compact JSON),
  `count=` and `digest=` to `$GITHUB_OUTPUT`. It refuses, exit 2, when that variable is unset.
- **README "Continuous integration"** was changed as far as a live test forces now
  (`ReadmeValidateJobTest` requires every `validate.yml` job to be named as a code span). The
  change covers the four-job list, the check names, and `tests-result` as the check branch
  protection should require. CP7 still owns the full rewrite of the Development and CI
  sections.
- **`tools/test_timings.json`** was refreshed through `timings merge` (not edited by hand)
  from a run of the two classes this checkpoint extended. The reason:
  `CommittedTimingsTest` pins each entry's test count, and it failed for
  `tests.test_ci_workflows.CoverageTest` (5 -> 7) and `tests.test_run_tests.BuildingBlocksTest`
  (5 -> 8). The refresh also adds `ValidatePlanTest`, for 446 atoms.
- **Tests:**
  - `tests/test_ci_workflows.py`:
    - `CoverageTest` was rewritten over `test_shards`. It checks that the hand-curated lists
      and jobs are gone, that every `tests/test_*.py` module is in the inventory, that the
      placement partitions the inventory, that the CI selection equals the `shards` part, the
      placed modules and their reasons, the package job's modules, and that the conformance
      family equals the managed workflow;
    - `ValidatePlanTest` (new, 8 tests) checks the job graph and needs, `if: always()`, the
      check names, the plan outputs and argv, the `fromJSON` matrix, the `exec-shard` argv
      (`--expect-digest`, `--ci-placement`, `--profile ci`, no `--count`/`--shards`/`--plan`,
      the same planning inputs as `plan`), the aggregate argv, the downloads with
      `continue-on-error`, the artifact names and paths, and that the actions are major tags;
    - the artifact-location test now lists validate's uploads and downloads and asserts that
      artifact names are unique across a `main.yml` run. The checkout count is now 7.
  - `tests/test_run_tests.py` `BuildingBlocksTest`, 3 new tests: `--github-output` (appends,
    and refuses when unset), `aggregate` without a plan, and `aggregate` with a missing shard
    result.
  - `tests/test_plan_document_consistency.py`: the pinned `validate.yml` job list was updated.
- **Verification:**
  - `python3 -m unittest tests.test_plan_document_consistency tests.test_ci_workflows
    tests.test_test_shards tests.test_run_tests tests.test_release_tools`: 270 tests, OK.
  - `python3 tools/ci_workflows.py --check`: clean.
  - **Local dry run of the CI sequence**, under a reaping subreaper because this session is a
    Controller worker. It ran `plan --profile ci --ci-placement --github-output`, then all 4
    `exec-shard ... --expect-digest` in parallel, each in a separate process that recomputed
    the plan, then `aggregate`. Result: digest `4e87f234adb0` matched in every shard, and the
    run passed, exit 0. It ran 2018 tests (the CI selection, without the 9 package and 20
    excluded tests). The shard walls were 143.2 / 142.6 / 143.7 / 146.9 s against an estimate
    of 151.9 s each, a balance ratio of 1.02. The largest atom was
    `conformance:workflow_acceptance_matrix_test.py`, at 78.5 s.
  - Not verified here: a real GitHub Actions run. The artifact actions' behaviour on a
    missing artifact (with `continue-on-error`) is by design and has not been observed.

### CP5 -- serialization registry and timing-flake hardening (complete)

- **`EXCLUSIVE_ATOMS`** (`tools/test_shards.py`) maps an atom to the reason it must run alone.
  It is **empty**: no failure in this checkpoint needed an entry (decision D5).
  - `build_inventory` refuses an entry that names no atom or has an empty reason
    (`exclusive_atoms_problems`), as a stale `CI_PLACEMENT` is refused.
  - `build_plan` keeps the selected exclusive atoms out of the shard count and the LPT
    assignment. It puts them, in canonical order, on one extra final shard marked
    `"exclusive": true`, so `shard_count` is one more than the formula's. The same plan serves
    CI, where that shard is one more job. A plan with no exclusive atom is byte-identical to
    before, so every CP3/CP4 digest and golden test is unchanged.
  - `validate_plan` accepts the key only as `true` on the last shard. Given the selection, it
    also checks that the exclusive shard holds exactly the selection's registered atoms.
  - The local runner (`run_shards`) starts the exclusive shard only once no other shard is
    running, and starts nothing beside it. `--replay --shard i` of it runs it alone anyway.
  - Every summary and `--plan-only` prints `EXCLUSIVE_ATOMS: <n> registered, <m> in this plan's
    exclusive shard`, and the summary table marks the row `<i> (exclusive)`.
- **Stress protocol, before any test fix** (the CP4 runner at HEAD `55c855b` plus the registry;
  each run under a reaping subreaper, because this session is a Controller worker):

  | configuration | command | runs | result | wall |
  | --- | --- | --- | --- | --- |
  | default (8 shards, 16 CPUs) | `python3 tools/run_tests.py` | 5 | 5 PASS | 81-83 s |
  | oversubscribed | `taskset -c 0-3 python3 tools/run_tests.py --shards 12 --jobs 12` | 3 | 3 PASS | 104-105 s |
  | 4 CPUs | `taskset -c 0-3 python3 tools/run_tests.py` | 5 | 5 PASS | 92-95 s |

  Zero failures, zero leaked processes. None of the three known candidates showed, so each was
  then run in targeted stress: 8 parallel copies of the class, pinned to one CPU
  (`taskset -c 0`), several rounds.
- **Candidate 1, the `DrainDetachJobTest` escapee race: reproduced and fixed** (a test defect).
  - *Exposed:* 1 failure in 32 targeted runs. The drain-detach message named the escapee
    `2529360 (bash -c exec -a fake-escapee sleep 3600)`, not `(fake-escapee ...`.
  - *Cause:* the Controller records an owned process's entry, `cmdline` included, **when it
    first sees it**, and keeps it (worker-lifecycle plan, C step 3; `controller/worker.py`,
    `entry = recorded or {...}`). The escapee is owned from its fork, because it is in the
    worker's group until its `setsid`. Under load, a scan can see it before its `exec -a`, and
    then its recorded command line is a pre-exec stage. The test's `(fake-escapee` prefix
    assumed the first scan came after the exec.
  - *The plan's suggested fix does not work.* Two fixture variants were tried and measured:
    - Waiting in the fake until `/proc/<pid>/cmdline` reads `fake-escapee` before the step
      continues: 10 failures in 64. The first sighting still precedes the exec, and the
      command substitution adds another pre-exec stage.
    - An orphan named from birth (exec'd under `argv0`, then forked): 23 failures in 64. The
      transient same-named parent becomes a second recognised `gpg-agent` in
      `excluded_processes`, and it breaks name-based lookups.

    Every stage of a process the worker spawns is visible from its fork, so no fixture can
    make the first sighting come after the exec.
  - *Fix:* `tests/fake_claude.py`'s `bash_bg` gains `orphan_pid_file`, which receives the
    orphan's `$!`. That pid is kept through `setsid` and `exec -a`, because a background child
    is not a group leader and `bash -c 'exec ...'` execs in place.
    `test_an_unrecognised_escapee_detaches_the_job_and_the_next_step_is_refused` now asserts:
    - the remaining pid is exactly the escapee's;
    - the message carries `<pid> (<the recorded cmdline>)` verbatim;
    - the recorded command line is one of the escapee's own stages
      (`fake-escapee 3600`, or `(setsid )?bash -c ... exec -a fake-escapee sleep 3600 ...`);
    - the live process's `argv[0]` is `fake-escapee`.
  - *Cleared:* 0 failures in 64 targeted runs.
  - **For the reviewer:** this changes one assertion's form, from a name prefix to the recorded
    command line plus exact pid identity. I read it as correcting an assertion that encoded a
    timing the Controller never guaranteed, not as loosening one, but the plan's hardening
    policy says assertions are never loosened, so it needs an explicit decision.
- **The same first-sighting class in `tests.test_worker.OwnershipTest`: found by targeted stress,
  partly fixed, partly open.**
  - *Exposed:* 11 failures in 24 targeted runs. The failures were
    `no owned process named 'fake-claude-orphan' was flushed`, an empty `[pid] = recorded`, and
    in the reparented case a first-sighting `ppid`.
  - *Fixed:* the orphan is now identified by its exact pid through `orphan_pid_file` (`_orphan`
    by pid, `_escaped`, `test_a_recorded_process_stays_owned_after_it_passes_no_other_test`,
    `test_an_unrecognised_daemon_detaches_after_the_drain_bound_and_ends_nothing`). The
    recorded command line is checked against the orphan's own stages
    (`_orphan_cmdline_re`).
  - *Still open:* 3 failures in 24, all
    `test_a_setsid_escapee_is_owned_by_tag_and_adopted_and_waited_for`
    (`'group' != 'tag'`). The recorded `source` is also first-seen, and before its `setsid`
    the escapee is in the worker's group. The first-sighting `ppid` check in the reparented and
    no-subreaper tests has the same shape.

    The only test-side changes would be to patch the supervisor's scan or to stop proving tag
    ownership. The alternative is a Controller change: refresh `source`/`cmdline` on later
    scans. This milestone does not change `controller/`. **Not fixed; it needs a decision.**
    It shows only at 8 copies on one CPU, never in the stress protocol's 26 runs.
- **Candidate 2, the `CheckpointPredicateUnitTest` error: not reproduced.** 40 targeted runs and
  all 26 protocol runs were clean. If it recurs, the executor's recording result now captures
  its traceback in `shard-<i>.json` and the summary.
- **Candidate 3, the `test_worker` hanging-worker reap check: not reproduced, and superseded.**
  - Runs: 40 targeted runs under the reaping wrapper, 80 without one, and one under a
    deliberately non-reaping subreaper, all clean.
  - Why: since `e8364eb` (2026-09-25, the day after the flake was observed), `worker.launch`
    is itself a reaping child subreaper for the launch's duration. The killed grandchild is
    reaped there, whoever the outer reaper is. No change was made.
- **Stress protocol, after the fixes** (same commands and configurations, now 2034 tests):

  | configuration | runs | result | wall |
  | --- | --- | --- | --- |
  | default (8 shards) | 5 | 5 PASS | 81-83 s |
  | 4 CPUs (`taskset -c 0-3`) | 5 | 5 PASS | 94-97 s |
  | oversubscribed (`--shards 12 --jobs 12`, 4 CPUs) | 3 | 3 PASS | 108-111 s |

  Zero failures, zero leaks; every summary reports `EXCLUSIVE_ATOMS: 0 registered`.
- **Tests:**
  - `tests/test_test_shards.py` has 120 tests. The 7 new ones are in `ExclusiveAtomsTest`:
    - the registry audit against the real inventory;
    - a named unknown atom and an empty reason;
    - `build_inventory` refusing a bad registry;
    - exclusive atoms together on the last shard, with the parallel shards planned as if they
      were unselected;
    - an unselected entry changing nothing, and an only-exclusive selection;
    - a misplaced, non-`true` or missing exclusive marker refused.
  - `tests/test_run_tests.py` has 20 tests. The new `ExclusivePhaseTest` registers an atom
    through a driver and runs the real CLI. It asserts that the exclusive shard's test starts
    after both parallel shards' tests have ended, and checks the summary lines. With the
    runner's gate removed, it fails.
  - `--plan-only` now prints the registry line.
  - The `DrainDetachJobTest` and `OwnershipTest` changes above.
- **Verification:**
  - `python3 -m unittest tests.test_test_shards tests.test_run_tests
    tests.test_fake_claude_contract tests.test_job.DrainDetachJobTest
    tests.test_worker.OwnershipTest`: 222 tests, OK.
  - The stress protocol before and after the fixes, as above: 26 full runs, all PASS.

### CP4 -- executor, local runner and aggregation (complete)

- **Revalidation at revision 4** (2026-09-27, head `ed90210`). CP4's plan section is unchanged
  from revision 3, so there is no code change. Under a reaping subreaper (this session is a
  Controller worker):
  - `python3 -m unittest tests.test_test_shards tests.test_run_tests`: 145 tests, OK. This
    includes the executor, recording-rule, verdict, aggregation, diagnostics and end-to-end runner
    tests.
  - Full selection, `python3 tools/run_tests.py` at defaults, run twice: PASS both times, 2049
    tests in 8 shards, wall 83.6 s and 84.8 s, balance 1.07 and 1.08. Load average about 7 on 16
    CPUs.

- **`tools/test_shards.py`** gains execution, leak detection and aggregation, still stdlib only.
  - **Executor.** `execute_shard(plan, i, results_dir)` runs one shard in-process:
    - `load_shard` loads each atom by name, filtered to the planned ids. Before anything runs,
      it refuses with `ShardRefusedError` (exit 2) unless the loaded ids equal the planned ids
      exactly and in order. A substituted test (`_FailedTest`, `ModuleImportFailure`,
      `ModuleSkipped`, ...), a load error, or a missing, duplicated or reordered id is named.
    - The controller atoms run through `unittest.TextTestRunner` (verbosity 2, into
      `shard-<i>.log`) with a `RecordingResult`. Each atom is its own top-level suite run, so
      its time includes its class and module fixtures. `_previousTestClass` is reset between
      atoms; without that reset, the next atom's first test would run the previous class's
      `tearDownClass` a second time (found by reading `unittest.suite`).
    - Each conformance atom runs as `sys.executable <file>` in `scripts/`, with its output
      appended to the log.
    - `shard-<i>.json` is written atomically, through the new `write_json_atomic`, which
      `write_timings` now uses too.
  - **Recording rules** (`RecordingResult`) implement the plan's rules:
    - an `_ErrorHolder` error or failure goes to `fixture_errors`, and an `_ErrorHolder` skip
      to `fixture_skips`;
    - after the run, `_backfill` gives every planned id that a fixture kept from running an
      `error` or `skip` entry naming the holder;
    - subtests fold into the parent at `stopTest`: `error`, then `fail`, then the parent's own
      outcome;
    - any event on an object the rules cannot map goes to `unmapped`, which refuses the shard.
    - LP-R3-001 was probed on Python 3.14.7: a `skipTest` inside a `subTest` reaches
      `addSkip(_SubTest)`, and the parent gets no outcome. The parent is then recorded as
      `skip`, as `unittest` itself treats it (`wasSuccessful()` stays True).
  - **Consistency.** `shard_status` is two-sided. If the record's controller verdict differs
    from `unittest`'s `wasSuccessful()` either way, the shard is refused (exit 2) with both
    values named. An unmapped event or a planned id without an entry also refuses.
  - **Environment.** `shard_environment` sets `TMPDIR` and `XDG_STATE_HOME` under
    `<results>/shard-<i>/`, plus the marker. It sets `PIP_CACHE_DIR=<results>/pip-cache-<i>`
    only under `--isolated-pip-cache`. Nothing else changes.
  - **Leaks.** `scan_marked_processes` finds live processes by their environment marker, and
    `kill_processes` SIGKILLs each one's group (or the process alone, if it shares the caller's
    group). The executor scans before writing its record, and the runner scans again after each
    shard exits. A leak is a warning (D7).
  - **Aggregation.** `aggregate(plan, records, results_dir)` works as follows:
    - each shard is `PASS`, `FAIL`, `CRASHED`, `REFUSED` or `INTERRUPTED`. A record whose exit
      status disagrees with its own verdict, or that does not cover its planned ids, is
      `CRASHED`;
    - the coverage proof (I2) names every NOT RUN id and every unplanned or duplicate id;
    - the exit status is 130 if any shard was interrupted, else 2, 1 or 0, as in the plan's
      table. A record carrying another plan's digest or shard index refuses the whole
      aggregate;
    - `render_summary` writes the Markdown summary: the shard table, then each failing test
      and each fixture error with its last 40 traceback lines, its log path and both
      reproduction commands (`cd scripts && python3 <file>` for a conformance suite). It also
      lists NOT RUN ids, coverage violations, fixture skips with the count each skipped, leaks,
      the wall time, the largest atom and the balance ratio.
- **`tools/run_tests.py`** (new) is the CLI:
  - the default run, a selection, `--serial` (a plan with `shards: 1` pinned, through the same
    executor and shaping), `--plan-only` (it also prints the largest atom and the D4 note for
    atoms above the target), `--replay PLAN [--shard i]`, `--jobs`, `--results-dir`, `-v`
    (per-line `[i]` streaming), `--isolated-pip-cache` and the planning overrides;
  - `exec-shard` takes either `--plan`, or the planning inputs with `--expect-digest`. Both,
    neither, or a digest mismatch exits 2, and a mismatch runs nothing. The executor resets
    SIGINT to `SIG_DFL`, sets `sys.path[0]` to the repository root and changes into it;
  - `plan` writes `plan.json` and prints `{plan_digest, shard_count, shards}`. `aggregate`
    writes `SUMMARY.md`, and also `$GITHUB_STEP_SUMMARY` when that is set. `timings merge
    --into FILE [--profile-name N] DIR...` folds results directories into a profile, and
    refuses to merge into an invalid existing file;
  - each shard runs in its own session (`start_new_session`), with stdin at `/dev/null`.
    Ctrl-C (or SIGTERM) sends SIGTERM to every running shard's group, then SIGKILL after 5 s,
    marks the running and queued shards `INTERRUPTED`, and exits 130;
  - after every run, the local timing cache is updated from the records' passing, complete
    atoms. A failure to update it is only a warning.
- **Interpretations the plan leaves open:**
  - **The leak marker nests.** The shard environment carries `<run_id>/<i>`, as planned. Each
    executor extends the marker it inherits to `<inherited>/<its pid>` before running
    anything, and a scan matches a marker or any marker under it. The first full `--serial`
    run found why this is needed. `tests.test_run_tests` starts an `exec-shard` inside a
    shard. With exact matching, that nested executor inherited the outer marker, found the
    outer executor in its leak scan, and SIGKILLed it, so all 2025 ids were NOT RUN (the
    aggregate reported that correctly). `NestedRunTest` pins the fix, and fails under the old
    rule.
  - **`--repo-root`** (hidden) lets the end-to-end tests drive a synthetic repository. The
    default is the checkout.
  - **Intermixed arguments.** The runner, `exec-shard` and `plan` use
    `parse_intermixed_args`, so test names may follow options.
- **`tools/test_timings.json`** (new) is the committed profile, `profile: "seed-local"`. It
  was seeded by `timings merge` from the full `--serial` run below: 445 atoms, 634 s. The one
  atom missing is `tests.test_test_shards.CommittedTimingsTest`, which failed in that run
  because this file did not exist yet; failed atoms are not merged, by design.
- **`tools/ci_workflows.py`** places `test_run_tests` in the `docs` shard until CP6 replaces
  the hand-curated matrix. `validate.yml` was re-rendered.
- **Tests:**
  - `tests/test_test_shards.py` has 113 tests (29 new):
    - `ExecutorRecordingTest` covers each recording rule over synthetic modules: a failing
      `tearDownModule`, a `setUpClass` error, mixed and passing subtests, a skip inside a
      subtest, an unexpected success, expected failures and skips, and a `setUpClass` or
      `setUpModule` `SkipTest` (not merged into timings). Every case checks the verdict
      against plain `unittest`'s `wasSuccessful()`. It also checks forced disagreement both
      ways, and that class fixtures run once;
    - `ExecutorRefusalTest`: a missing id, an import failure (`_FailedTest`), a module that
      skips at import, and the order;
    - `AggregateTest`: exact coverage, the failure summary, a missing id, a crashed shard,
      duplicate and unplanned ids, an exit-status disagreement, interruption, a foreign
      digest or index, a single-shard replay, leaks, and `load_results`;
    - `EnvironmentShapingTest`, `LeakScanTest` (including nested markers), and
      `CommittedTimingsTest`.
  - `tests/test_run_tests.py` (new, 19 tests) runs the real CLI end to end against a synthetic
    repository. It covers:
    - exit codes 0, 1, 2 and 130;
    - the summary's reproduction commands, and `--replay --shard` reproducing the same ids in
      the same order;
    - a failing conformance suite, a crash, a selection that matches nothing, and
      `--plan-only`;
    - environment shaping, `--isolated-pip-cache` and `--serial`;
    - SIGINT at `SIG_DFL` in shards when the runner started with it ignored;
    - a leak reported and killed, and Ctrl-C leaving no process behind (including one that
      traps SIGTERM);
    - the plan digest across two checkout paths, and both `exec-shard` forms;
    - `aggregate` with `$GITHUB_STEP_SUMMARY` and refusing a foreign digest, `timings merge`,
      and the nested-run regression.
- **Verification:**
  - `python3 -m unittest tests.test_test_shards tests.test_run_tests tests.test_ci_workflows`:
    181 tests, OK. `tools/ci_workflows.py --check` is clean.
  - Full selection, `run_tests.py --serial` (under a reaping subreaper, because this session
    is a Controller worker): 2026 tests in 634 s. The only failure was
    `CommittedTimingsTest`, before the seed existed. After seeding, that test passes alone.
  - Full selection, `run_tests.py` at defaults: 8 shards, PASS, 82.6 s wall, balance ratio
    1.02. The largest atom is `conformance:workflow_acceptance_matrix_test.py` at 82.1 s. No
    leaks.
  - Id comparison: the sharded run reported exactly the serial run's 2026 ids, with no
    duplicates. The serial run's controller ids equal `unittest discover`'s 2019 ids, in
    order.

### CP3 -- adaptive deterministic planner (complete)

- **Revalidation at revision 4** (2026-09-27, head `435de8a`). CP3's plan section is unchanged
  from revision 3, so there is no code change. Under a reaping subreaper (this session is a
  Controller worker):
  - `python3 -m unittest tests.test_test_shards`: 121 tests, OK. This includes CP3's shard-count,
    parameter, plan, self-validation, 500-case equivalence property, determinism and baseline
    balance tests.
  - Full selection, `python3 tools/run_tests.py` at defaults: PASS, 2049 tests in 8 shards,
    wall 91.0 s, balance 1.11. Load average about 7 on 16 CPUs.

- **`tools/test_shards.py`** gains the planner, still stdlib only.
  - **Parameters.** `profile_parameters(profile, ...)` gives the plan's table:
    - `local`: a 60 s target, a minimum of 2, and a maximum of `min(8, cpu_count)`;
    - `ci`: a 180 s target, a minimum of 2, and a maximum of 16.

    Every value can be overridden, and `shards=` pins the count. An unknown profile, a
    non-positive or non-finite target, or a count below 1 refuses with `PlanError`, naming each
    problem. `PlanParameters.document()` puts `shards` into the digested parameters only when it
    is pinned.
  - **Count.** `shard_count` computes `clamp(ceil(total / max(target, largest)), min, max)`,
    capped at the number of atoms. A pinned count is capped the same way. The arithmetic is in
    integer milliseconds.
  - **Assignment.** `assign_atoms` is deterministic LPT. It sorts by `(-estimate, canonical
    index)`, places each atom on the shard with the least `(load, index)` using a heap, and then
    puts each shard back into canonical order.
  - **Timings.** `timings_for(profile)` loads the files a profile plans from, in priority order:
    the local profile and then `tools/test_timings.json` for `local`, and only the committed file
    for `ci`. `timing_source` names each file actually read as `{path, sha256}`. A path inside
    the repository is recorded relative to it, and any other path is recorded as
    `"local-profile"`. When no file was read, the source is `"defaults"`.
  - **Plan.** `build_plan(selection, parameters, timings)` returns the plan's JSON document:
    `schema_version`, `profile`, `selection_names`, `selected_ids`, `parameters`, `shard_count`,
    `shards` (`{index, atoms, test_ids, estimate_seconds}`), `timing_source` and `plan_digest`.
    The digest is the SHA-256 of the canonical JSON (sorted keys, no whitespace, no NaN) of
    everything else. An empty selection refuses.
  - **Self-validation.** `validate_plan(plan, selection=None)` checks all of the following and
    names every problem:
    - the exact keys, the schema version and the digest;
    - `shard_count` equals the number of shards, and the shards are indexed in order;
    - no shard is empty, no id is in two shards, no id is outside the selection, and no selected
      id is missing;
    - each shard is in canonical order.

    Given the selection, it also checks that each shard holds exactly its whole atoms, and that
    every atom is placed once. `build_plan` runs this check before it returns.
- **Interpretations the plan leaves open:**
  - **`timing_source` is a list.** Local planning reads two files (local, then CI) per the plan's
    section B, so the source records each one read, in priority order. The plan's own
    `"defaults"` value stands when none was read.
  - **Each estimate is floored at 1 ms for placement.** The 500-case property test found that
    atoms estimated at 0 ms (a profile can record `seconds: 0`) all landed on shard 0, whose load
    never grew, leaving other shards empty. With every load positive, LPT puts the N longest
    atoms on N distinct shards, so no shard can be empty.
  - **An empty selection refuses.** With `ci_placement`, a name inside a `CI_PLACEMENT` module
    resolves to no atoms. There is nothing to partition, so `build_plan` refuses rather than
    emit a zero-shard plan. CP4 decides how the CLI reports it.
- **`estimate_atoms`** now memoises each profile's per-module mean. The result is unchanged, but
  it was quadratic in the number of atoms, which the 500-case property test made visible.
- **`tests/golden/test_shards_baseline_timings.json`** (new) is the recorded baseline for the
  balance test. It was measured during this checkpoint: per-atom first-`startTest`-to-last-
  `stopTest` spans from a serial `discover` run (1947 tests, 479.5 s, OK, 8 skipped), plus each
  conformance suite's wall time, for 428 atoms totalling 587 s. The largest atom is
  `conformance:workflow_acceptance_matrix_test.py` at 79.2 s, then three `test_worker` classes
  at about 33.5 s each. On this baseline, the default local profile plans 8 shards, as the
  plan's table predicts.
- **`tests/test_test_shards.py`** grows to 84 tests (133 together with `test_ci_workflows`):
  - `ShardCountTest` covers the formula at each boundary: rounding up, the largest-atom floor,
    the minimum and maximum clamps, the cap at the number of atoms, and a pinned count;
  - `ProfileParametersTest` covers the defaults, including a `cpu_count` of 1 or unknown, the
    overrides, and each invalid value named;
  - `PlanTest` covers the LPT placement and tie-breaks, zero estimates, whole-millisecond
    comparison, the document and its digest, a pinned count entering the parameters, every
    planning input changing the digest, `timing_source` naming, an empty selection, and the
    planner refusing its own broken plan;
  - `PlanValidationTest` covers 14 tamperings, each refused whether or not the digest was
    recomputed, and a split or foreign atom refused against the selection;
  - `EquivalencePropertyTest` runs 500 seeded cases of the real inventory, checked directly
    rather than through `validate_plan`. Each case has random module, class, method and
    conformance names, with and without the CI placement; random, corrupt, empty or missing
    timings; and `N` pinned from 1 to 32, or random parameters. Each plan must be an exact,
    disjoint union of the selection, with no empty shard, whole atoms, and canonical order;
  - `DeterminismTest` computes the same digests twice in-process, and in fresh interpreters
    under `PYTHONHASHSEED` 0 and 4242;
  - `BaselineBalanceTest` checks, for every N from 2 to 32 with `largest ≤ total/N`, that the
    longest shard is within 1.10 × `total/N`. The measured ratio is 1.000 at N = 2 to 7. N = 8 is
    excluded, because 79.2 s > 587/8.
- Mutation check: each of the following fails the new tests: ascending instead of descending
  LPT, dropping the 1 ms floor, the atom cap, the largest-atom floor, or the canonical reorder.
- Verification: `python3 -m unittest tests.test_test_shards tests.test_ci_workflows` passes (133
  tests, about 1 s). The serial baseline run above doubles as a full-suite run at CP2's content.
  It ran under a reaping subreaper wrapper with SIGINT at `SIG_DFL`, because this session is a
  Controller worker.

### CP2 -- result records and timing model (complete)

- **Revalidation at revision 4** (2026-09-27, head `31b88ea`). CP2's plan section is unchanged
  from revision 3, so there is no code change. Under a reaping subreaper (this session is a
  Controller worker):
  - `python3 -m unittest tests.test_test_shards`: 121 tests, OK. This includes CP2's estimate,
    merge, prune, corrupt-file fallback and real-inventory defaults tests.
  - Full selection, `python3 tools/run_tests.py` at defaults: PASS, 2049 tests in 8 shards,
    wall 97.3 s, balance 1.13. The machine was loaded (load average about 10 on 16 CPUs).

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

- **Revalidation at revision 4** (2026-09-27, head `8b0f522`). CP1's plan section is unchanged
  from revision 3, so there is no code change. Under a reaping subreaper (this session is a
  Controller worker):
  - `python3 -m unittest tests.test_test_shards tests.test_ci_workflows`: 180 tests, OK.
  - `python3 tools/ci_workflows.py --check`: clean.
  - Full selection, `python3 tools/run_tests.py` at defaults: PASS, 2049 tests in 8 shards,
    wall 101.0 s, balance 1.15. The machine was loaded (load average about 10 on 16 CPUs).

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
