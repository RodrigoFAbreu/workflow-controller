# Active Milestone

## Status

**Implementing.** `workflow-controller-ci-reliability` (`docs/ROADMAP.md` step C2, section 11.2
"CI reliability"). The plan is `docs/ai-workflow/CONTROLLER_CI_RELIABILITY_PLAN.md`, revision 3,
approved at `fdb925a` (`EXTERNAL_APPROVE`). The base commit is `93b82de`. Governing workflow
version `2.2`, lifecycle authority Workflow 2.6.0. Pull request title
`fix: stop the known CI flakes and make a re-run of failed jobs count` (1.4.2).

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-child-process-reaping.md`.

## Goal

Fix the three timing flakes CI shows (the empty first-sighting command line, a product defect; and
two test readiness defects in the on-spawn window), each with a widened regression that fails
every time before the fix. Make "Re-run failed jobs" count: shard records carry their run
attempt, each attempt uploads its own artifacts, and `tests-result` takes each shard's latest
attempt, reports what it superseded, and fails when a current-attempt job failed without a fresh
record. A leaked process fails the run (D7); nothing is retried automatically (I6, ADR 0005).

## Checkpoint progress

| Checkpoint | Status | Notes |
|---|---|---|
| CP1 The recorded command line fills once readable | Complete | See below |
| CP2 The tests wait for the right signal | Complete | See below |
| CP3 A re-run of failed jobs counts | Complete | See below |
| CP4 A leaked process fails the run | Complete | See below |
| CP5 Documentation and full verification | Complete | See below |

### CP1 -- the recorded command line fills once readable

- `controller/worker.py`:
  - `_Ownership.scan`: a recorded entry whose `cmdline` is `""` takes the first non-empty read
    (`" ".join(cmdline)[:200]`), once. A non-empty one is never replaced, and never by an empty
    read. Identity, `source`, membership and daemon classification are unchanged (I1).
  - `_Ownership._fill_sample`: the first-sighting sample's copy of that entry follows the same
    rule, so `owned_processes_seen.sample` does not keep the empty text either.
  - `_Supervision._signature` includes each owned entry's `cmdline`, so the fill is published:
    one extra `worker_state` publication per entry recorded empty, none otherwise (I2). Re-attach
    gets the fill too, since `_recorded_ownership` feeds the same scan.
- `tests/test_worker.py`:
  - `_empty_first_cmdline_read(marker)`: a `worker._read_cmdline` wrapper that reads empty the
    first time a pid's real command line contains the marker (matched on content, since the pid
    file may not exist yet at the first scan), and returns the pids it emptied.
  - `OwnershipTest._escaped` (the setsid, reparent and no-subreaper tests) and the daemon-detach
    test run under it, and assert that the widening ran for the orphan's pid.
  - `_gated_escapee` allows the single `""` -> value step in the published command lines, and
    nothing else.
  - `CmdlineFillTest` (new, over the fixture `/proc`; its setup is shared with
    `OwnershipProvenanceTest` through `_FixtureOwnershipCase`): an entry first read empty stays
    empty through an empty read, takes the next non-empty one and keeps it through an empty and a
    different read, with the sample filled; a non-empty entry keeps its text; an empty recorded
    entry fills after a re-attach; and the fill is published exactly once.
- `tests/test_job.py` `DrainDetachJobTest`: the same widening for `fake-escapee`; the
  `OwnedWorkDetachedError` message never says `<pid> ()`.

Red run against `93b82de`'s `controller/worker.py` (the new tests in place): 8 failures in
`CmdlineFillTest`, `OwnershipTest` and `DrainDetachJobTest` -- the three `_escaped` tests and
the daemon-detach test with `Regex didn't match ... not found in ''`, `DrainDetachJobTest` with
`'<pid> ()' unexpectedly found in 'worker pid ... still running after 2 s: <pid> () -- ...'`, and
three of the four `CmdlineFillTest` tests (the non-empty-keeps test passes at the base, as it
should). With the fix: all 29 tests of those classes and `OwnershipProvenanceTest` pass.

Verification: `tests/golden/generate_*.py --check`: `external_implementation_review_decisions` and
`no_policy_lifecycle` current; `plan_stage_decisions` differs, and differs identically at the base
(the documented `AMENDING_PLAN` difference). The full sharded run (`python3 tools/run_tests.py`,
2469 tests, 6 shards) passed. It ran inside a Controller-launched worker, so it went through a
reaping-subreaper wrapper (`PR_SET_CHILD_SUBREAPER` + `waitpid(-1)` loop), in the foreground, with
`FORCE_COLOR` and `PYTHONPATH` unset.

### CP2 -- the tests wait for the right signal

No change outside `tests/`.

- `tests/test_resume.py`:
  - `_OrphanWorkerCase._record_with_worker_process` returns a `LAUNCHED` record with
    `worker_process` only once its job log has the `worker_spawned` line (`_spawn_line_written`),
    so the `SIGKILL` never lands between the `on_spawn` record write and the line's append. The
    line's `seq` is the `event_seq` that write set. The helper accepts it when it is equal to the
    record's `event_seq` or lower, so a later `worker_state` publication that bumps the record
    cannot make the wait hang. Every `_OrphanWorkerCase` class inherits the wait.
  - `_CHILD_STEP_SLOW_SPAWN_LINE`: `_CHILD_STEP` with the checkout's
    `job.runtime.append_jsonl_best_effort` wrapped after the import, so it writes a marker file
    (`argv[5]`) and sleeps 1 s before the `worker_spawned` append. `_orphan_worker` runs it when
    it is given `slow_spawn_line=<marker>`.
  - `CrossProcessEventSeqTest.test_the_kill_waits_for_a_delayed_worker_spawned_line` (new): asserts
    that the marker exists and `event_seq` is 3, and that after `resume` the log is exactly
    `planned, launched, worker_spawned, reconciled` with `seq` `1..4`.
- `tests/test_job.py` `InProcessConcurrencyTest`:
  - `_start_first_step` also waits until the record carries `worker_process` and
    `worker_anchor`.
  - `test_the_second_step_waits_for_a_delayed_spawn_flush` (new) runs the existing test's body
    (now `_assert_a_second_step_refuses`) with `job._persist` patched to sleep 1 s before the
    `worker_spawned` write. It asserts that the delay ran exactly once.

Red run against the base's helpers (the base `_record_with_worker_process` and
`_start_first_step`, with the new tests in place): both new tests fail with the CI symptoms. The
resume test fails with `['planned', 'launched', 'reconciled'] != ['planned', 'launched',
'worker_spawned', 'reconciled']`, and the job test with `KeyError: 'worker_process'`. With the
fix, every `_OrphanWorkerCase` class and `InProcessConcurrencyTest` pass.

Stress (informative): `CrossProcessEventSeqTest` + `InProcessConcurrencyTest`, 6 parallel copies
under `taskset -c 0,1`, 5 rounds: 30/30 OK.

Verification: the full sharded run (`python3 tools/run_tests.py`, 2471 tests, 6 shards) passed,
through the same reaping-subreaper wrapper, in the foreground, with `FORCE_COLOR` and
`PYTHONPATH` unset.

### CP3 -- a re-run of failed jobs counts

- `tools/test_shards.py`:
  - Shard records are schema version 2, with a required `run_attempt` (an int >= 1).
    `validate_shard_result` still reads version 1, which has no `run_attempt`, and
    `record_attempt` reads it as attempt 1 (I9). `execute_shard` takes `run_attempt`, default 1.
  - `select_results` collects every `shard-<i>.json` directly in the results directory (the
    local flat layout) or one directory below it (CI's per-artifact layout), and takes each
    planned shard's highest attempt (I4). It raises `AggregateError` (exit 2, I7) for:
    - two records of one shard and attempt (a flat version 1 record counts as attempt 1);
    - an attempt above `--run-attempt`;
    - another plan's digest in any record, superseded or not;
    - a `results-<i>-attempt-<a>` directory whose record is another shard or attempt;
    - a record whose file name names another shard.

    An unreadable record leaves its shard without a record (CRASHED) unless its directory
    proves it is older than the selected one. Every record not taken is returned as
    superseded, with its verdict and failing ids.
  - `aggregate` takes `superseded` and `upstream` (job to result). Any `upstream` result other
    than `success` turns an otherwise passing run into exit 2, and a failing run keeps its
    status (I4a). The summary names each such job under the status line, gives each shard's
    attempt in a new last column, and adds a "Superseded attempts" section (I5). The
    reproduction hints name `results-<i>-attempt-<a>`.
- `tools/run_tests.py`:
  - `exec-shard --run-attempt N` (default 1; below 1 is a usage error).
  - `aggregate` uses `select_results` and gains:
    - `--run-attempt N`;
    - repeatable `--upstream-result JOB=RESULT`, with `JOB` `plan` or `tests` and `RESULT` one
      of `success`, `failure`, `cancelled`, `skipped`. Anything else, or a job given twice, is a
      usage error;
    - `--write-selected DIR`, which writes the plan and the selected records flat.
      LOCAL-R3-001: it is written whatever the verdict, including exit 2 from `--upstream-result`
      (`timings merge` folds passing atoms only). It is not written when the results are refused
      outright (`AggregateError`).
  - The summary's directory is created if no results artifact was downloaded at all.
  - The local runner (`cmd_run`, `--serial`, `--replay`) and `timings merge` are unchanged. They
    read the flat layout through `load_results`, and the local runner writes attempt 1.
- `tools/ci_workflows.py`, and `.github/workflows/validate.yml` re-rendered with `--write`:
  - `exec-shard` passes `--run-attempt ${{ github.run_attempt }}`.
  - Shard jobs upload `results-${{ matrix.shard }}-attempt-${{ github.run_attempt }}`.
  - `tests-result` downloads `pattern: results-*` into `results/shards`, without
    `merge-multiple`. It aggregates with `--results-dir results/shards --run-attempt ...
    --write-selected results/selected --upstream-result plan=${{ needs.plan.result }}
    --upstream-result tests=${{ needs.tests.result }}`, and uploads `results/selected/` as
    `timings-ci-attempt-${{ github.run_attempt }}`.
  - `test-plan` stays one artifact (Design C). `ci.yml`, `main.yml` and `pr-title.yml` are
    unchanged.
- Tests:
  - `tests/test_test_shards.py`:
    - `ResultRecordTest`: version 1 and 2 validation, and invalid `run_attempt` values.
    - `AttemptSelectionTest`:
      - the run 36483126576 replay, passing whether candidates are discovered in sorted or
        reversed order;
      - the superseded failure listed;
      - a failing attempt 2 after a passing attempt 1;
      - duplicate, above-bound, directory-mismatch, file-name-mismatch and superseded-digest
        refusals;
      - an unreadable record, older and latest;
      - the flat layout equal to `load_results`;
      - the four I4a cases: full re-run with a missing record, full re-run with a failed plan,
        "Re-run failed jobs" green, and failing records keeping exit 1.
  - `tests/test_run_tests.py` `BuildingBlocksTest` (real `exec-shard` runs into per-artifact
    directories, the conformance suite failing in attempt 1):
    - `--run-attempt` recorded;
    - latest attempt taken, with `--write-selected` feeding `timings merge`;
    - above-bound refusal writing nothing;
    - three non-`success` upstream combinations exiting 2 and still writing the selection;
    - `--upstream-result` usage errors;
    - no results artifact at all.
  - `tests/test_ci_workflows.py`: the rendered artifact names match
    `RESULTS_ARTIFACT_DIR_RE`, there is no `merge-multiple`, and the `--run-attempt`,
    `--write-selected` and both `--upstream-result` options are wired.

Verification: `python3 -m unittest tests.test_test_shards tests.test_ci_workflows
tests.test_run_tests` passed (248 tests). `python3 tools/ci_workflows.py --check` is clean. The
full sharded run (`python3 tools/run_tests.py`, 2493 tests, 6 shards) passed and wrote schema 2
records (`run_attempt` 1). It ran through the same reaping-subreaper wrapper, in the foreground,
with `FORCE_COLOR` and `PYTHONPATH` unset.

### CP4 -- a leaked process fails the run

- `tools/test_shards.py`:
  - `shard_status` returns `1` for a record whose tests pass but whose `leaked_processes` is
    non-empty. A refusal still returns `2`. So a leak-only shard's `exec-shard` exits `1`, its
    `tests (<i>)` job is red, and "Re-run failed jobs" re-runs it (I8).
  - `LEAKED`, a new verdict. `_verdict` gives it to a record whose tests pass but which lists a
    leak, whether its exit status is `1` (the executor's own) or `0` (a pre-1.4.2 record): the
    leak list decides, so an old record cannot pass by a stale status. Any other exit status is
    still `CRASHED`, and a refused record is still `REFUSED`.
  - `aggregate` exits `1` for `LEAKED`, like `FAIL`. `INTERRUPTED`, `REFUSED`, `CRASHED` and
    `FAIL` keep their precedence.
  - The summary section is "Leaked processes (failure, killed)", with a line saying a leak is a
    test that left a process running. Each entry keeps its shard, pid, age and argv.
  - `shard_verdict` (the executor's own stderr) labels a leak-only shard `LEAKED` and lists each
    leaked process, so the red job says why.
- `tools/run_tests.py` `_record_leaks`: a late leak the parent finds moves a passing record's
  `exit_status` from `0` to `1`.
- Tests:
  - `tests/test_test_shards.py`: `LeakedShardTest` (new; `shard_status`, and `execute_shard` with a
    patched leak scan: a passing shard that leaked exits `1` with `exit_status: 1`, a leak with a
    refusal exits `2`). In `AggregateTest`, the pinning test becomes "a leaked process is
    reported, killed and fails the run", plus a leak record read as `LEAKED` at exit status `1`
    and `0`, and precedence (a leak with a FAIL in the same record is FAIL; `REFUSED`,
    `CRASHED`, `INTERRUPTED` win; `LEAKED` beside `FAIL` exits `1`). In `AttemptSelectionTest`, a
    leaked attempt 1 superseded by a clean attempt 2 passes and lists `LEAKED` under "Superseded
    attempts".
  - `tests/test_run_tests.py` `ProcessHygieneTest`: the end-to-end pinning test becomes "a leaked
    process is reported, killed and fails the run" (exit `1`, `LEAKED`, record `exit_status: 1`);
    `test_a_late_leak_fails_a_passing_record` (new) covers `_record_leaks`.

Red run against the base's `tools/` (the new tests in place): 9 failures and errors -- every new
or flipped test except the refusal one, which holds at the base as it should. With the fix,
`tests.test_test_shards`, `tests.test_run_tests` and `tests.test_ci_workflows` pass.

Verification: the full sharded run (`python3 tools/run_tests.py`, 2500 tests, 6 shards) passed
with exact coverage and no leaked process, through the same reaping-subreaper wrapper, in the
foreground, with `FORCE_COLOR` and `PYTHONPATH` unset.

### CP5 -- documentation and full verification

- `docs/guide/ci-and-releases.md`: the `tests` and `tests-result` jobs name the per-attempt
  artifacts (`results-<i>-attempt-<a>`, `timings-ci-attempt-<a>`); a new "Re-running failed jobs"
  section says that a re-run of failed jobs counts (each shard's highest attempt decides, a shard
  not re-run keeps its record, superseded attempts are listed, the exit-2 refusals including the
  current-attempt job check), that a leaked process fails the run, and that nothing is retried
  automatically, and why.
- `docs/guide/development.md`: exit status 1 includes a leak; a "Leaks" item (a test must end
  every process it starts; the `LEAKED` verdict and the summary section); the committed-profile
  refresh reads the highest attempt's `timings-ci-attempt-<a>` artifact, which holds the plan and
  only the selected records (`LOCAL-R1-003`).
- `docs/guide/milestone-branches.md`: the `checks_failing` row says "Re-run failed jobs" is
  enough for a failed test and a leak alike.
- `docs/guide/workers.md`: an owned entry's command line is recorded empty when first seen inside
  an `execve`, and takes the first non-empty read, once.
- `docs/adr/0005-adaptive-test-sharding.md`: a 1.4.2 amendment note (D7 decided, attempts
  selected, I6 kept, I10's command line fills once). `docs/adr/0004-worker-lifecycle-ownership.md`:
  a 1.4.2 amendment note for the command-line fill (I2).
- `tools/test_timings.json` is unchanged: `CommittedTimingsTest` passes.

Stress (informative): CP1's classes (`tests.test_worker.OwnershipTest`,
`tests.test_worker.CmdlineFillTest`, `tests.test_job.DrainDetachJobTest`, 23 tests), 6 parallel
copies under `taskset -c 0,1`, 5 rounds: 30/30 OK. CP2's stress is recorded under CP2 (30/30 OK).

Verification:
- `tests/golden/generate_*.py --check`: `external_implementation_review_decisions` and
  `no_policy_lifecycle` current; `plan_stage_decisions` differs, identically to the base (as at
  CP1).
- `python3 tools/ci_workflows.py --check` is clean.
- `git diff 93b82de -- .workflow-controller/ pyproject.toml setup.py .github/workflows/ci.yml
  .github/workflows/main.yml .github/workflows/pr-title.yml` is empty.
- The full sharded run (`python3 tools/run_tests.py`, 2500 tests, 6 shards) passed with exact
  coverage and no leaked process, through the same reaping-subreaper wrapper, in the foreground,
  with `FORCE_COLOR` and `PYTHONPATH` unset.

## Pull request body (1.4.2 release notes)

From 1.4.0 the pull request body is the release notes (`docs/README.md`). After the release, a
docs pull request adds `docs/releases/1.4.2.md` from it, as for 1.4.1. The Draft PR for this
milestone carries:

> **Stop the known CI flakes and make a re-run of failed jobs count (1.4.2)**
>
> **The command line fills once readable.** An owned process the Controller first saw inside an
> `execve` read an empty command line, and 1.4.1 kept that empty text for the life of the entry,
> so a drain detach could name `<pid> ()`. A recorded entry whose command line is empty now takes
> the first non-empty read, once, and the fill is published: at most one extra `worker_state`
> publication per such entry, re-attach included. A non-empty command line is never replaced, and
> never by an empty read. Ownership, and each entry's `pid`, `start_ticks` and `source`, are
> unchanged; job records keep 1.4.1's format, and 1.4.1 and 1.4.2 re-attach to each other's jobs.
>
> **The tests wait for the right signal.** Two tests acted inside the on-spawn window: one killed
> the worker before its `worker_spawned` event line was written, one started a second step
> before the record carried `worker_process`. Both now wait for the signal they depend on, and
> each has a regression that widens the window on purpose.
>
> **A re-run of failed jobs counts.** Shard records are schema version 2 and carry their
> `run_attempt` (version 1 is still read, as attempt 1). Each CI attempt uploads
> `results-<i>-attempt-<a>`, and `tests-result` takes each shard's latest attempt, lists the
> attempts it superseded, and fails when a job of the current attempt did not succeed but older
> records would pass. It uploads `timings-ci-attempt-<a>`. "Re-run failed jobs" on a flaky shard
> can now turn the run green, and the summary still shows what it replaced.
>
> **A leaked process fails the run.** A test that leaves a process running gets the verdict
> `LEAKED`: the process is reported and killed, and the shard's job and `tests-result` are red
> even if every test passed. Nothing is retried automatically.
>
> **Operator note.** The CI artifacts are renamed (`results-<i>-attempt-<a>`,
> `timings-ci-attempt-<a>`); the required checks and their names are unchanged. Refresh the
> committed timings from the highest attempt's `timings-ci-attempt-<a>` artifact.
