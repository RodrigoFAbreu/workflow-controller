# Controller CI reliability: fix the known flakes, and make a re-run of failed jobs count (Revision 3)

Work item: `workflow-controller-ci-reliability`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `93b82de04371d4ae4deedee6448b21e8057481c7` ("docs: 1.4.1 release notes and two roadmap
follow-ups (C3 release notes, C5 configurable events) (#12)"), the tip of `main` when this plan was
written, passed explicitly as `/milestone-plan 93b82de…` (after a squash merge the next item is
planned from `main`'s head with the base passed explicitly, `docs/guide/milestone-branches.md`). The
previous milestone (`workflow-controller-child-process-reaping`) was accepted at `9b6ceab`,
squash-merged by PR #11 (`e8cd8f9`) and released as 1.4.1. PR #12 added the 1.4.1 notes and two
roadmap follow-ups. Neither is this milestone's work.
Lifecycle authority: this repository's installed Workflow **2.6.0**.
Driving Controller: the installed **1.4.0** package (`workflow-controller --version`: `package
(release v1.4.0; built from ba3a615d27f7)`). 1.4.1 is installed between Workflow Manager milestones
(shared lane plan); nothing here depends on which of the two drives it.
Roadmap slot: `docs/ROADMAP.md` step **C2** of "At a glance", section **11.2 CI reliability**.
Released baseline preserved: `workflow-controller 1.4.1` (`v1.4.1`). This milestone ships as the
patch release **1.4.2**, derived by `main.yml` from the `fix:` pull request title below.
Pull request title: `fix: stop the known CI flakes and make a re-run of failed jobs count`

## Goal

An unattended merge (C4) stalls on every red run, and the Workflow 2.6 milestone needed five
re-runs. Between 2026-09-26 and 2026-09-30 about 89 CI/Main attempts ran; eleven failed on three
timing flakes, and re-running only the failed jobs cleared a failure in some runs but not in
others. This milestone:

1. **Fixes the three flakes CI actually shows**, each with a regression test that widens the race
   window through a patchable seam, so it fails every time before the fix and passes after
   (no CPU-stress statistics):
   - `tests.test_worker.OwnershipTest` records an owned process with an **empty command line**
     when its first sighting falls inside an `execve` (or its exit), and keeps it empty for the
     process's life. This is a **product defect**: the drain-detach error then prints
     `"<pid> ()"`, and `observe`/`explain` print "command line unreadable" for a readable
     one. The Controller fills an empty recorded command line from the first non-empty read, and
     publishes the change.
   - `tests.test_resume.CrossProcessEventSeqTest` SIGKILLs a child Controller as soon as the job
     record carries `worker_process`, which is written *before* the `worker_spawned` event line
     is appended. The line is best-effort by contract, so a kill in between leaves a `seq` gap. A
     **test defect**: the test waits for the line itself.
   - `tests.test_job.InProcessConcurrencyTest` (seen once, 2026-09-29) reads `worker_process`
     from the record before `on_spawn` has flushed it. A **test defect** of the same on-spawn
     window: the test waits for `worker_process` and `worker_anchor`.
2. **Makes "Re-run failed jobs" count.** Each shard result records its run attempt, each attempt
   uploads under its own artifact name, and `tests-result` aggregates, for every shard, the result
   of the **latest attempt that ran it**. Superseded attempts stay in the summary, so a failure
   that passed on a re-run is still reported, never hidden. A job of the current attempt that
   failed without leaving a fresh record or plan fails `tests-result`, so an earlier attempt's
   passing record can never stand in for it.
3. **Decides the two policy questions 11.2 names:**
   - a leaked process **fails** the run (D7, until now a warning), and its own `tests (<i>)` job
     too, so "Re-run failed jobs" re-runs that shard;
   - a failed shard is **not** retried automatically (I6 and ADR 0005 are kept). A re-run is an
     explicit act, by a person today and by the Controller's fix loop later (C10), and the summary
     reports it.

## Non-goals

- **No automatic retry of a failed test or shard** (Decision 2). `tools/run_tests.py` keeps I6.
- **No change to what the Controller owns, waits for or ends.** The command-line fill changes only
  the text recorded for an entry that is already owned, and it adds one publication per such
  entry (I2).
- **No change to the event-log contract.** `worker_spawned` stays best-effort after the record
  write; the record is the authority (`controller/job.py` `_persist`). Only the tests change their
  readiness signal.
- **No fix for flakes seen once and never reproduced** (see Investigation, "Seen once"). They stay
  listed as carried over; C10's fix loop is where a new one gets handled.
- **No timing-profile refresh** (D9). `tools/test_timings.json` changes only if a renamed or
  removed test class makes `CommittedTimingsTest` fail.
- **No change to the required checks** or the repository ruleset. The check names stay
  `validate / plan`, `validate / tests (<i>)`, `validate / tests-result`, `validate / package`.
- **No release-notes mechanics.** That is C3 (11.1.2). This milestone's notes are its narrative's
  "Pull request body" section, as for 1.4.1.

## Investigation: what happens today (measured at `93b82de`)

### CI failures, 2026-09-16 to 2026-09-30

Read from the repository's GitHub Actions runs (`gh run list`, `gh run view --log-failed`).

| Test | Failed attempts | Assertion |
|---|---|---|
| `test_resume.CrossProcessEventSeqTest.test_seq_continues_across_processes` | 7 (runs 36261210041, 36263982784, 36283114840, 36483126576 attempts 1 and 2, 36501573457, 36590332935) | `tests/test_resume.py:2435`: `['planned','launched','abandoned'] != ['planned','launched','worker_spawned','abandoned']` |
| `test_worker.OwnershipTest` (three methods) | 3 (36481280078 attempts 1 and 2, 36647154187) | `_escaped` (`tests/test_worker.py:2379`) and the daemon-detach test (`:2641`): the orphan-command-line regex not found in `''` |
| `test_job.InProcessConcurrencyTest.test_a_second_step_refuses_with_exit_45_and_launches_nothing` | 1 (36633759482) | `tests/test_job.py:1639`: `KeyError: 'worker_process'` |

**Seen once, not reproduced, out of scope:** `test_resume` R15 re-attach (`'RUNNING' not found in
['ENDING','ENDED']`, 36273953238), and before sharding `EquivalenceTest`,
`AbandonWithAnOrphanedAnchorTest` and `NoPolicyGoldenTest` (36250907762, 36251388152). **Already
fixed:** the first-sighting `source` race (Design H of the sharding milestone), `RunRecordCtrlCTest`
(F2), and the gated-escapee publication (F1). **Not flakes:** four consecutive `filter.lfs.*`
failures on 2026-09-28 were a real defect, since fixed.

### Flake 1: the empty first-sighting command line (product)

`_Ownership.scan` (`controller/worker.py:1059-1150`) reads `_read_cmdline(root, pid)` for every
owned candidate. A new entry stores `" ".join(cmdline)[:200]`; an entry already recorded is kept
as it was first seen, with only `source` refreshed (`entry = dict(recorded, source=source)`,
`:1122-1123`). `_Supervision._signature` (`:1664-1676`) does not include the command line, so a
changed one would not be published anyway.

The test orphan (`tests/fake_claude.py:983-990`) runs three `execve`s: subshell, `setsid`,
`bash -c`, then `exec -a fake-claude-orphan sleep`. While a process is inside `execve` (new memory
map installed, arguments not yet set), and again on its exit path after `exit_mm` but before it is
a zombie, `/proc/<pid>/cmdline` reads **empty** while the state is `R`, so the `_GONE_STATES`
filter does not skip it. Polling such a chain 300 times on this machine gave 5,607 empty reads in
state `R` during the `execve`s and 1,171 during exit. If the first scan lands in that window, the
entry says `cmdline: ""` for the process's whole life, is persisted, and is re-seeded on
re-attach (`worker.py:1950`).

User-visible: `OwnedWorkDetachedError` (`controller/job.py:3686`) prints `"<pid> ()"`, the
`worker_drain_detached` event's `remaining` carries the empty text, and `observe.py:1136` /
`job.py:3419,3596` print "command line unreadable".

Also exposed today, but not seen failing: `test_worker.py:2504-2505`
(`test_a_recorded_process_stays_owned_after_it_passes_no_other_test`) and
`test_job.DrainDetachJobTest` (`tests/test_job.py:2676-2695`), which assert the same command-line
text.

### Flake 2: `worker_spawned` lost to the kill (test)

`CrossProcessEventSeqTest` (`tests/test_resume.py:2409-2439`) uses
`_OrphanWorkerCase._orphan_worker` (`:1109-1127`). That helper polls `_record_with_worker_process`
(`:1097-1105`) for a `LAUNCHED` record with `worker_process`, then SIGKILLs the child Controller.
`worker.launch` calls `on_spawn` (`controller/worker.py:1389-1391`), and `job.on_spawn`
(`controller/job.py:4902-4920`) calls `_persist(..., event="worker_spawned")`. `_persist`
(`job.py:4153-4173`) writes the record first (`event_seq` 3, now with `worker_process`) and then
appends the event line. A SIGKILL between the two loses the line. The log then shows `seq` 1, 2, 4,
which is exactly what CI saw. This is the contract ("a lost line is a gap in `seq`"), so the test's
readiness signal is wrong.

Other tests kill after `worker_process` (`tests/test_resume.py:1169,1222,1260,1302`,
`tests/test_lifecycle_orchestration.py:1296-1328`, `tests/test_integration_disposable_repo.py:2273`).
None of them asserts the event log, so none flakes. They share the helper and inherit the fix.

### Flake 3: the second step reads the record too early (test)

`InProcessConcurrencyTest._start_first_step` (`tests/test_job.py:1610-1623`) waits only for the
fake worker's invocation line. The fake starts when `Popen` returns (`worker.py:1352`), which is
before `_spawn_anchor` and `on_spawn` flush `worker_process` and `worker_anchor`. So the read at
`:1639` can find neither. The sibling test already waits for `worker_process` (`:1663`).

### Why "Re-run failed jobs" sometimes does not count

- **Pipeline.** `validate.yml` is rendered by `tools/ci_workflows.py:280-350`. Its jobs run in this
  order:
  - `plan` uploads `test-plan`.
  - Each `tests (<i>)` job runs `exec-shard` and uploads `results/` as `results-<i>` under
    `if: always()`.
  - `tests-result` (`needs: [plan, tests]`, `if: always()`) downloads `test-plan`, then
    `pattern: results-*` with `merge-multiple: true` into one flat directory, and runs `aggregate`.
- **Records.** A shard record (`tools/test_shards.py:406-408`, schema version 1, unknown keys
  rejected at `:467-470`) carries no run attempt. Nothing in the repository reads
  `GITHUB_RUN_ATTEMPT`.
- **A re-run adds a second artifact.** In a re-run of failed jobs, the re-run shard uploads a
  second `results-<i>` in the same run; `upload-artifact@v4` accepts it.
- **The download keeps one of them, by ID.** `download-artifact@v4` lists artifacts with
  `latest: true`. For each name, the toolkit keeps the one with the **highest artifact ID**
  (`actions/toolkit`, `list-artifacts.ts:176-187`). IDs are not assigned in upload order:
  - In run 36483126576, attempt 1's failed `results-1` got ID 10998401184, and attempt 2's
    passing one got the lower ID 10998306989. Attempt 2's `tests-result` aggregated attempt 1's
    record and failed with attempt 1's traceback.
  - In run 36481280078 the IDs happened to increase, and a re-run of failed jobs went green.
- **So the outcome is not deterministic.** Whether a re-run counts depends on how GitHub
  assigns IDs. Every full re-run so far went green, but the design below does not rely on whether
  an earlier attempt's artifacts are still visible to a later one (Revision 3, the manual external
  review's finding: if they are, a stale record could stand in for a job that failed in the
  current attempt).
- **A flat merge of both would not be safe.** It would overwrite `shard-<i>.json`, and the last
  writer would win. The attempt must be named, and chosen explicitly.
- **`tests-result` ignores its upstream jobs' results.** It runs under `if: always()`
  (`validate.yml:60-64`), and `cmd_aggregate` (`tools/run_tests.py:239-252`) only checks that a
  plan file exists. Nothing compares `needs.plan.result` or `needs.tests.result` with the records
  it aggregates.

### Leaked processes today (D7)

Each shard's environment carries `WORKFLOW_CONTROLLER_TEST_SHARD=<run_id>/<i>`
(`tools/test_shards.py:1091-1136`, extended with `/<pid>` by `exec-shard`, `run_tests.py:191-194`).
Before writing its record, the executor scans `/proc/*/environ` for the marker, SIGKILLs the
matches' groups and lists them in `leaked_processes` (`test_shards.py:1473-1477,1507-1550`). The
local runner re-scans after each shard (`run_tests.py:325-365`). The summary lists them under
"Leaked processes (warning, killed)" (`test_shards.py:1840-1843`). The status block
(`:1696-1704`) ignores them. Two tests pin that: `tests/test_run_tests.py:338` and
`tests/test_test_shards.py:1907`.

Baseline: **0 leaked processes** in 94 CI shard records (16 CI and Main runs, 2026-09-28 to 09-30,
including the post-C1b runs), in 154 local records, and in a fresh 307-test run of
`tests.test_worker tests.test_resume`. The scan cannot see a process with an empty environment (the
Controller's anchor, `env -i` fixtures, zombies). That blind spot is unchanged here.

### Retries today

I6 ("No hidden retries", `docs/adr/0005-adaptive-test-sharding.md:66-67`, rejected alternative
"Automatic retries" at `:249-251`; `tools/run_tests.py:6`) holds because nothing retries:
`run_shards` runs each index once. The only re-run is GitHub's, by hand.

## Invariants

- **I1 Ownership unchanged.** Which processes are owned, when an entry is added or dropped, its
  `pid`, `start_ticks` and `source`, and every lifecycle decision are unchanged. Only an entry's
  recorded `cmdline` may change, once, from `""` to the first non-empty read.
- **I2 Command-line fill.** A recorded non-empty command line is never replaced, and never by an
  empty read. The fill is published, so the signature includes `cmdline`. That adds at most one
  publication per entry recorded empty. It applies after a re-attach too, because re-attach reuses
  the same scan.
- **I3 Event-log contract unchanged.** `_persist` writes the record, then appends best-effort.
- **I4 The latest attempt decides.** For each planned shard, `aggregate` uses the record with the
  highest `run_attempt` it finds. The exit status comes from those records only.
- **I4a The current attempt's jobs succeeded** (Revision 3). In CI, `aggregate` also receives the
  current attempt's `needs.plan.result` and `needs.tests.result`. When either is not `success`
  and the selected records would otherwise pass, it exits 2: a job scheduled in this attempt
  failed without a fresh record or plan, so an earlier attempt's passing record or plan cannot
  make `tests-result` green. A shard not scheduled in a "Re-run failed jobs" attempt keeps its
  earlier success in `needs.tests.result`, so it keeps its earlier record (I4).
- **I5 Nothing hidden.** Every superseded record is listed in the summary with its attempt, its
  verdict and its failing test ids.
- **I6 No hidden retries (ADR 0005, kept).** Nothing in the runner or the workflows runs a test or
  a shard a second time by itself.
- **I7 Fail closed on ambiguity.** These make `aggregate` exit 2:
  - two records for the same shard and attempt;
  - a record whose attempt is above the aggregating attempt;
  - a per-attempt directory whose name disagrees with its record;
  - any existing check: digest, index, coverage.
- **I8 Leaks fail.** A shard that leaked a process fails, even if every test passed. It gets its
  own verdict, and the run exits 1 unless something worse happened. The shard executor itself
  exits 1 for it, so the shard's `tests (<i>)` job is red and "Re-run failed jobs" re-runs it: a
  leak is cleared by the same re-run as a failed test, and never needs a full re-run.
- **I9 Local layouts keep working.** `tools/run_tests.py` (default, `--serial`, `--replay`) keeps
  writing and reading the flat `shard-<i>.json` layout. Schema version 1 records (an old
  `timings-ci` download, a recorded plan's results) are still read, as attempt 1.

## Design

### A. The recorded command line fills once it is readable (CP1)

- **Where.** In `_Ownership.scan`, where a recorded entry is carried over.
- **The fill.** When the recorded `cmdline` is `""` and the current read is non-empty, the entry
  takes `" ".join(cmdline)[:200]`. It is never overwritten after that, and never by an empty read.
- **Other copies.** Every place that copies the first-seen command line into what is published
  or persisted (the `owned_processes` entries and any per-sample copy the scan keeps) follows the
  same rule.
- **The signature.** `_Supervision._signature` adds `p["cmdline"]` to each owned-process tuple, so
  the fill is published.
- **Daemon classification is unchanged.** A process whose non-empty command line matches a daemon
  pattern moves to `excluded` exactly as today.

**Tests (CP1),** in `tests/test_worker.py`:
- **Widened repro, the regression.** Patch `worker._read_cmdline` with a function that calls the
  real one and returns `[]` the first time a pid's real result contains `fake-claude-orphan`. It
  matches on content, because `orphan_pid_file` may not exist yet at the first scan.
  - The `_escaped` family must pass under that patch.
  - The patched test fails at `93b82de`. That red run is recorded in the checkpoint notes.
  - This copies the F1 pattern: `test_an_escapee_read_mid_exec_is_published_as_adopted_for_that_scan`
    patches `_read_environ`.
- **The same widening for the daemon-detach test** (`:2627`): `result.remaining` names the
  command line.
- **The same widening for `test_job.DrainDetachJobTest`:** the `OwnedWorkDetachedError` text says
  `<pid> (<command line>)`, never `<pid> ()`.
- **An adjusted test.** `_gated_escapee` asserts one command line across every publication
  (`tests/test_worker.py:2447`). It is relaxed to allow the single `""` → value step, and nothing
  else.
- **A unit test of the scan:**
  - an entry first read empty takes the next non-empty read;
  - a non-empty entry keeps its text through a later empty read and a later different read.
- **Publication:** exactly one extra `worker_state` publication for the fill, and none when
  nothing changed.
- **Goldens.** Every `tests/golden/generate_*.py --check` passes. The 2.5.1 `plan_stage_decisions`
  golden differs at the base as well (the documented `AMENDING_PLAN` difference).

### B. The tests wait for the right signal (CP2)

- **`_OrphanWorkerCase`** (`tests/test_resume.py`). Before the SIGKILL, `_record_with_worker_process`
  (or `_orphan_worker`) waits until `jobs/<id>/events.jsonl` has a `worker_spawned` line whose `seq`
  equals the record's `event_seq`. Every test using the helper inherits the wait; waiting a little
  longer is harmless for the ones that do not read the log.
- **`InProcessConcurrencyTest._start_first_step`** (`tests/test_job.py`). Before the second step,
  it waits for both `worker_process` and `worker_anchor` in the record.
- **The regression tests widen the window.** Each is new, runs in the suite permanently, and costs
  about 1 s:
  - **Flake 2.** The child Controller runs a variant of `_CHILD_STEP` (`tests/test_resume.py:1046-1058`)
    selected by a new `_orphan_worker` argument. After `from controller import job`, the variant
    wraps `job.runtime.append_jsonl_best_effort` to sleep 1 s before the `worker_spawned` append
    (the attribute `DrainDetachJobTest` already patches in-process, `tests/test_job.py:2640`). It
    does not use a `sitecustomize` shim (Revision 2, `LOCAL-R1-002`): `_CHILD_STEP` puts the
    checkout on `sys.path` only after start-up, so a start-up shim would import an installed
    `controller`, or fail silently with a pipx-only install. The variant binds the checkout's
    module by construction. The wrapper also writes a marker file before it sleeps, and the test
    asserts the marker exists, so a wrap that never ran cannot pass. The helper's wait must still
    see the line, and the log must be exactly `planned, launched, worker_spawned, <last>`. Before
    the fix this fails every time; the red run against the base's helper is recorded.
  - **Flake 3.** In the in-process test, `job._persist` is patched to sleep 1 s before writing
    `worker_spawned`, and to record that it did, which the test asserts. The test then passes, and
    it fails with the base's `_start_first_step`.
- **A shared helper.** The widening helpers live beside the existing wait helpers in
  `tests/fixtures.py`, if two modules use them; otherwise in the module.

### C. A re-run of failed jobs counts (CP3)

**The record.**
- `RESULT_SCHEMA_VERSION` becomes 2, which adds a required `run_attempt` (an int ≥ 1).
- `validate_shard_result` accepts version 1 without the key, read as attempt 1 (I9), and version 2
  with it. Writers always write version 2.
- `exec-shard` gains `--run-attempt N`, default 1. The CI model passes
  `${{ github.run_attempt }}`, and the local runner writes 1.

**The workflow** (`tools/ci_workflows.py`, re-rendered with `--write`):
- A `tests (<i>)` job uploads `results/` as `results-<i>-attempt-<a>`.
- `tests-result` downloads the `test-plan` artifact as today. It downloads `pattern: results-*`
  **without** `merge-multiple` into `results/shards/`, so each artifact lands in its own
  `results/shards/<artifact name>/` directory, and every attempt present is downloaded.
- `aggregate` receives `--results-dir results/shards`, `--run-attempt ${{ github.run_attempt }}`,
  `--write-selected results/selected`, and the upstream results `--upstream-result
  plan=${{ needs.plan.result }} --upstream-result tests=${{ needs.tests.result }}` (I4a,
  Revision 3).
- The `timings-ci` upload becomes `timings-ci-attempt-<a>`, holding `results/selected/`: the plan
  plus the selected records, flat. The D9 refresh input is therefore unambiguous, and
  `timings merge` reads it unchanged.
- `main.yml` calls the same `validate.yml`, so the release gate gets the same behaviour.

**The aggregation** (`tools/test_shards.py` `load_results`/`aggregate`, `tools/run_tests.py`
`cmd_aggregate`):
- **Collecting records.** It collects every `shard-<i>.json` directly in `--results-dir` (the
  local flat layout) or one level below it (the CI per-artifact layout). When a directory is named
  `results-<i>-attempt-<a>`, the record in it must say shard `<i>` and attempt `<a>` (I7).
- **Choosing a record.** For each planned index it takes the record with the highest
  `run_attempt` (I4). It exits 2 on:
  - a duplicate `(index, attempt)`;
  - an attempt above `--run-attempt` when that option is given;
  - a digest mismatch in any record, superseded or not.
- **The verdict.** Coverage (exactly once), the verdicts and the exit status are computed from the
  selected records only, as today.
- **The current attempt's jobs** (I4a, Revision 3). `--upstream-result <job>=<result>` is
  repeatable; `<job>` is `plan` or `tests`, and `<result>` is one of GitHub's `success`,
  `failure`, `cancelled` or `skipped` (anything else is a usage error, exit 2). When any given
  result is not `success` and the selected records would exit `0`, `aggregate` exits `2`, and
  the summary's status block names the job and its result: "a job of this attempt did not
  succeed and left no fresh result; an earlier attempt's record does not count for it". When the
  selected records already exit non-zero, their status stands, and the summary still names the
  job. Without the option (the local runner, `--replay`) nothing changes (I9).
  - **Why the job results suffice.** The `needs` context holds the current attempt's result of
    each job. In a "Re-run failed jobs" attempt, a job that is not re-run keeps its earlier
    `success`, so an unchanged shard retains its earlier record, as I4 requires. A job that is
    scheduled again (a full re-run, or the re-run failed shard itself) reports this attempt's
    result: if it fails before writing or uploading its record, `needs.tests.result` is
    `failure`; if the rerun's `plan` fails, `needs.plan.result` is `failure` and `tests` is
    `skipped`. Either way `tests-result` is red, even though an earlier attempt's passing record
    or `test-plan` is still downloaded.
  - **Why not a per-shard completeness check.** Knowing which shards the current attempt
    scheduled would need the jobs API and a token in `tests-result`. The job results give the
    same fail-closed answer with no new permission; the per-shard detail is in the red job
    itself.
- **The summary.** The summary adds, for each shard, the attempt it was taken from. It also gets a
  **"Superseded attempts"** section listing each earlier record's shard, attempt, verdict and
  failing test ids (I5): a flake that passed on a re-run is reported, not hidden. The artifact
  names in the reproduction hints follow the new names.
- **`test-plan` stays one artifact.** In a full re-run `plan` uploads a second `test-plan`, and
  `download-artifact` keeps the one with the highest ID, the same choice the results artifacts
  suffer from. It is harmless for the plan's content: `build_plan` (`tools/test_shards.py:928-989`) is a pure
  function of the commit's test selection and the committed `tools/test_timings.json`, so every
  attempt's plan is byte-identical, and every record's `plan_digest` is checked against the one
  downloaded (I7). A per-attempt `test-plan` would add a name without adding a check. A rerun
  whose `plan` job fails before uploading is caught by I4a (`needs.plan.result`), not by the
  artifact.

**Tests (CP3):**
- **`tests/test_test_shards.py`:**
  - schema 1 and 2 validation;
  - selection of the highest attempt;
  - duplicate and above-bound attempts refused;
  - a directory/record mismatch refused;
  - a passing attempt 2 clears attempt 1's failure, while the superseded failure is listed;
  - a failing attempt 2 fails even when attempt 1 passed;
  - a shard not re-run keeps its attempt-1 record;
  - the flat layout unchanged.
- **`tests/test_run_tests.py`:** the `aggregate` CLI over both layouts, plus `--write-selected`.
- **`tests/test_ci_workflows.py`:**
  - the rendered artifact names;
  - no `merge-multiple` on the results download;
  - the `--run-attempt` wiring;
  - `--check` clean.
- **A replay of run 36483126576's shape.** A synthetic `results/shards/` holds attempt 1's failing
  `results-1` and attempt 2's passing one, and aggregation passes, whatever order they are
  discovered in.
- **The current attempt's jobs (I4a, Revision 3),** in `tests/test_test_shards.py` and through the
  `aggregate` CLI in `tests/test_run_tests.py`:
  - a full re-run where a previously passing shard produced no attempt-2 record: only attempt-1
    passing records are present, `--run-attempt 2 --upstream-result plan=success
    --upstream-result tests=failure`, and `aggregate` exits `2`, naming the job;
  - a full re-run whose `plan` produced no new artifact: the earlier `test-plan` and attempt-1
    passing records are present, `--upstream-result plan=failure --upstream-result
    tests=skipped`, and `aggregate` exits `2`;
  - a "Re-run failed jobs" attempt: shard 0's attempt-1 PASS is kept, shard 1's attempt-2 PASS
    supersedes its attempt-1 FAIL, both upstream results are `success`, and `aggregate` exits `0`;
  - selected records that already FAIL keep exit `1` with a non-`success` upstream result;
  - an unknown job name or result value is a usage error;
  - without `--upstream-result`, the flat local layout behaves as today.
- **`tests/test_ci_workflows.py`** also asserts the rendered `tests-result` passes both
  `--upstream-result` options from `needs.plan.result` and `needs.tests.result`.

### D. A leaked process fails the run (CP4)

- **The executor's exit status** (Revision 2, `LOCAL-R1-001`, option (a)). `shard_status`
  (`tools/test_shards.py:1405-1420`) returns `1` for a record whose tests pass but whose
  `leaked_processes` is non-empty (`run_shard` computes the leaks at `:1473-1477`, before the
  status). A refusal still returns `2`. So a leak-only shard's `tests (<i>)` job is red, and
  "Re-run failed jobs" re-runs that shard (Goal 2). Under I4, a clean attempt 2 then supersedes the
  leaked attempt 1.
- **The local runner's late leaks.** `_record_leaks` (`tools/run_tests.py:325-334`) adds the leaks
  the parent finds after a shard exits. When it adds any to a record whose `exit_status` is `0`, it
  sets `exit_status` to `1`, so the record stays consistent with the rule above.
- **The verdict.** `_verdict` gives a shard that would otherwise PASS the verdict `LEAKED` when
  its record lists any `leaked_processes`. Its consistency check (`:1625-1628`, exit status versus
  the record's own verdict, else `CRASHED`) expects `1` for such a record, changed together with
  `shard_status`. Otherwise every leak record would read as `CRASHED`. A leak record with exit
  status `0` (a pre-1.4.2 record) is read as `LEAKED` too, not `CRASHED`: the leak list decides,
  so an old record cannot pass by a stale status.
- **The ranking.** `FAIL`, `CRASHED`, `REFUSED` and `INTERRUPTED` keep their precedence over
  `LEAKED`. The status block treats `LEAKED` like `FAIL` (exit 1), so a run with only leaks exits 1.
- **The summary.** The section is renamed "Leaked processes (failure, killed)". Each entry keeps
  its shard, pid, age and argv, and says a leak is a test that left a process running.
- **The local runner.** It already re-scans and merges leaks into the record (with the
  `exit_status` change above), so its exit status follows. The "leaked N processes" note for a shard without a record stays.
- **Unchanged.** Detection is unchanged, and so is its blind spot for empty-environment processes.

**Tests (CP4):**
- `tests/test_run_tests.py:338` and `tests/test_test_shards.py:1907` become "a leaked process is
  reported, killed and fails the run" (exit 1, verdict `LEAKED`).
- `shard_status` and `execute_shard`: a shard whose tests pass but which leaked exits `1`, and its
  record says `exit_status: 1`. A leak with a refusal still exits `2`.
- `_verdict`: a leak record with exit status `1` is `LEAKED`, not `CRASHED`; a leak record with
  exit status `0` is `LEAKED` as well.
- `_record_leaks`: a late leak moves a passing record's `exit_status` from `0` to `1`.
- A leak together with a FAIL keeps FAIL.
- The re-run path (I4): attempt 1 of a shard leaked, attempt 2 of the same shard is clean and
  passes, and the run passes, with the leaked attempt listed under "Superseded attempts".

### E. Documentation and full verification (CP5, terminal)

- **`docs/guide/ci-and-releases.md`:**
  - the per-attempt artifact names;
  - "Re-run failed jobs" now counts: the latest attempt of each shard decides, and superseded
    attempts are listed;
  - leaks fail the run;
  - no automatic retries, and why.
- **`docs/guide/development.md`:**
  - the leak rule for tests (a test must end every process it starts), and the `LEAKED` verdict
    locally;
  - the D9 refresh input (`:132-134`, today "a CI run's `timings-ci` artifact") becomes the
    **highest attempt's** `timings-ci-attempt-<a>` artifact, which holds the plan and only the
    selected records (Revision 2, `LOCAL-R1-003`).
- **`docs/guide/milestone-branches.md`:** the `checks_failing` row says that "Re-run failed jobs"
  is enough, for a failed test and for a leak alike (the leaked shard's job is red, I8).
- **`docs/guide/workers.md`:** the owned-process command line is the first non-empty read.
- **ADR 0005 amendment note (1.4.2):** D7 is decided (leaks fail), attempts are selected, and I6
  is kept (Decision 2). **ADR 0004 amendment note:** the command-line fill (I2).
- **The pull request body.** The 1.4.2 release notes go in `docs/ACTIVE_MILESTONE.md`'s "Pull
  request body" section. After the release, a docs pull request adds `docs/releases/1.4.2.md` from
  it, as for 1.4.1, until C3.
- **The full sharded suite** runs, the goldens pass `--check`, and `tools/ci_workflows.py --check`
  is clean.

## Checkpoints

<!-- registry table: generated by workflow_state.render_registry_markdown, never hand-edited -->

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The recorded command line fills once readable: controller/worker.py _Ownership.scan takes the first non-empty read for an entry recorded with an empty cmdline (never overwritten, never by an empty read) and _Supervision._signature includes cmdline so the fill is published; the widened _read_cmdline regression for OwnershipTest's _escaped family, the daemon-detach test and test_job DrainDetachJobTest with the red run at 93b82de, the scan unit test, the one-extra-publication test, _gated_escapee relaxed to the single empty-to-value step, goldens --check | - | 2 | 1 |
| CP2 | The tests wait for the right signal: tests/test_resume.py _OrphanWorkerCase waits for the worker_spawned event line whose seq equals the record's event_seq before the SIGKILL, tests/test_job.py InProcessConcurrencyTest waits for worker_process and worker_anchor before the second step; permanent widened regressions (a _CHILD_STEP variant wrapping job.runtime.append_jsonl_best_effort to delay the worker_spawned append, a patched job._persist delaying the worker_spawned write), each asserting its delay ran, with red runs against the base helpers; no change outside tests/ | - | 2 | 1 |
| CP3 | A re-run of failed jobs counts: shard record schema 2 with a required run_attempt (schema 1 read as attempt 1), exec-shard --run-attempt, per-attempt artifacts results-<i>-attempt-<a> downloaded without merge-multiple into per-artifact directories, aggregate selecting each shard's highest attempt with duplicate, above-bound and directory-mismatch refusals, a Superseded attempts summary section, --upstream-result failing tests-result with exit 2 when a current-attempt plan or tests job did not succeed but the selected records pass, --write-selected feeding timings-ci-attempt-<a>; tools/ci_workflows.py model and the rendered validate.yml; tests in test_test_shards, test_run_tests and test_ci_workflows including the run 36483126576 replay and the full-rerun missing-record and missing-plan cases | - | 3 | 1 |
| CP4 | A leaked process fails the run (D7): shard_status and exec-shard exit 1 for a leak-only shard so its tests job is red and a re-run of failed jobs re-runs it, run_tests _record_leaks moving a passing record to exit 1, a LEAKED verdict for an otherwise passing shard with leaked_processes with _verdict's consistency check changed together, exit 1 unless something worse, FAIL/CRASHED/REFUSED/INTERRUPTED keeping precedence, the summary section renamed to failure, the two pinning tests flipped, a leak with a FAIL, and a leaked attempt 1 superseded by a clean attempt 2 covered | CP3 | 1 | 1 |
| CP5 | Documentation and full verification (terminal): docs/guide/ci-and-releases.md, development.md, milestone-branches.md and workers.md, the ADR 0005 and ADR 0004 amendment notes, the 1.4.2 notes as the pull request body, the stress runs recorded, the unchanged-path check, ci_workflows --check and the full sharded suite | CP1, CP2, CP3, CP4 | 1 | 1 |

### CP1 -- the recorded command line fills once readable

- Files: `controller/worker.py`, `tests/test_worker.py`, `tests/test_job.py`.
- Done when: the tests of Design A pass; the widened `_escaped` test's red run at `93b82de` is
  recorded; the goldens pass `--check`; the full sharded suite passes.

### CP2 -- the tests wait for the right signal

- Files: `tests/test_resume.py`, `tests/test_job.py`, possibly `tests/fixtures.py`.
- Done when: the widened regression tests pass, and their red runs against the base's helpers are
  recorded; the full sharded suite passes; the diff outside `tests/` is empty.

### CP3 -- a re-run of failed jobs counts

- Files: `tools/test_shards.py`, `tools/run_tests.py`, `tools/ci_workflows.py`,
  `.github/workflows/validate.yml` (rendered), `tests/test_test_shards.py`,
  `tests/test_run_tests.py`, `tests/test_ci_workflows.py`.
- Done when: the tests of Design C pass; `python3 tools/ci_workflows.py --check` is clean; the
  full sharded suite passes locally, where it writes schema 2 records.

### CP4 -- a leaked process fails the run

- Files: `tools/test_shards.py`, `tools/run_tests.py`, `tests/test_run_tests.py`,
  `tests/test_test_shards.py`.
- Done when: the tests of Design D pass; the full sharded suite passes with no leak.

### CP5 -- documentation and full verification (terminal)

- Files: `docs/guide/ci-and-releases.md`, `docs/guide/development.md`,
  `docs/guide/milestone-branches.md`, `docs/guide/workers.md`,
  `docs/adr/0005-adaptive-test-sharding.md`, `docs/adr/0004-worker-lifecycle-ownership.md`.
  `tools/test_timings.json` changes only if `CommittedTimingsTest` requires it.
- Done when: the docs match the code; the full sharded suite passes; the 1.4.2 notes are the
  pull request body; `git diff 93b82de -- .workflow-controller/ pyproject.toml setup.py
  .github/workflows/ci.yml .github/workflows/main.yml .github/workflows/pr-title.yml` is empty.

## Decisions for the reviewer and the user

1. **Fill once, over always refreshing.** Refreshing the command line on every change (as Design H
   did for `source`) would add a publication for each `execve` of every owned process and change
   what a long-running process's entry says over time. Filling only an empty entry fixes the
   defect with at most one publication per affected entry. Recommended.
2. **No automatic retry.** A retry would clear the flakes this milestone fixes and any future one,
   but it hides an intermittent product defect (Flake 1 is one) and reverses I6 and ADR 0005's
   rejected alternative. Instead:
   - a re-run of failed jobs now counts;
   - the summary reports what it superseded;
   - the unattended loop (C4) stops on a red check and reports, until C10's fix loop.

   Recommended. The alternative, if the user prefers it: one automatic re-run of a failed shard
   inside the same job, reported as a superseded attempt. That needs an amendment of I6.
3. **Leaks fail the run (D7).** The baseline is clean: no leak in 248 records. A leak means a test
   left a process running, which is also what caused the zombie incident. Failing now keeps it
   clean. The risk is a new red run from a test that leaks only rarely, and the summary names
   the process and the shard. Recommended. The alternative is to keep it a warning.
4. **Per-attempt artifacts plus the attempt in the record, over only renaming the artifacts.**
   With only a directory name to go on, a misplaced record would be trusted. The record names its
   own attempt, and the directory name is cross-checked (I7). Recommended.
5. **Patch release.** The title is `fix:`, so `main.yml` releases **1.4.2** from `v1.4.1`.
6. **Roadmap wording.** 11.2 lists D7 among "the known races". It is a policy change, decided
   here (Decision 3). The roadmap is not edited by this plan; the milestone's acceptance marks
   11.2 complete.

7. **Revision 2 (local plan review round 1).** Every finding was verified against `93b82de` and
   accepted:
   - `LOCAL-R1-001` (Important): option (a). The executor exits `1` for a leak-only shard, and
     `_record_leaks` and `_verdict`'s consistency check change with it (Design D, I8). Option (b),
     a leak that only a full re-run clears, would keep the non-determinism Goal 2 removes, for
     leaks alone. CP4's tests and CP5's guide wording follow, and the functional re-run flow gains
     a leak probe.
   - `LOCAL-R1-002` (Optional): the child-script variant, not a `sitecustomize` shim, plus a marker
     assertion so a no-op widening cannot pass (Design B).
   - `LOCAL-R1-003` (Optional): CP5's `development.md` bullet names the highest attempt's
     `timings-ci-attempt-<a>` artifact.
   - `LOCAL-R1-004` (Optional): Design C states why `test-plan` stays one artifact.

8. **Revision 3 (manual external plan review round 1).** The one finding (Important, a missing
   current-attempt result hidden by an older passing record or plan) was verified against
   `93b82de` and accepted: `validate.yml`'s `tests-result` runs under `if: always()` and neither
   it nor `cmd_aggregate` (`tools/run_tests.py:239-252`) reads `needs.plan.result` or
   `needs.tests.result`, so Revision 2's highest-attempt selection alone would take an attempt-1
   PASS for a shard whose attempt-2 job failed before uploading, and an earlier `test-plan` for a
   failed rerun `plan`. The fix is the upstream-job-result check the reviewer named: I4a, Design
   C's `--upstream-result`, the full-rerun missing-record and missing-plan tests (both exit 2),
   and the retained behaviour for shards a "Re-run failed jobs" attempt did not schedule. A
   per-shard completeness check through the jobs API was rejected: it needs a token and a new
   permission in `tests-result` for no stronger answer. The Investigation's claim that a full
   re-run replaces earlier artifacts is withdrawn; nothing relies on it. CP3's registry entry and
   R5 name the check.

`docs/TECHNICAL_DECISIONS.md` does not exist in this repository, so no "Open decision" row is
touched.

## Open questions

None blocking. For Flake 1 the fix is proven by the widened regression, not by CI statistics. A
flake of a new kind found on CI during this milestone is recorded and carried over, not folded in,
unless it is one of the three classes above.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-ci-reliability-artifacts.json` starts from
`generate_artifacts_declarations(..., work_item_type="product")` and is fitted to this plan's
footprint the same way as the previous Controller milestone's declaration.

**Plan stage.**
- Protected: this plan, its registry and its mapping.
- It inherits the template exclusions and adds these as excluded implementation content:
  `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`, `docs/releases/`,
  `pyproject.toml`, `setup.py` and `docs/README.md`.

**Implementation stage.**
- Protected prefixes: `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`,
  `docs/releases/`, and the inherited `docs/adr/`.
- Protected paths: `README.md`, `docs/README.md`, `pyproject.toml`, `setup.py`, the four rendered
  workflows (`validate.yml`, `ci.yml`, `main.yml`, `pr-title.yml`), `CLAUDE.md` and the artifacts
  file itself. This milestone changes `validate.yml` (rendered, CP3). Of the other paths, it
  changes only those under `controller/`, `tests/`, `tools/`, `docs/guide/` and `docs/adr/`.

`docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md` and `docs/ai-workflow/` stay excluded, as narrative
and bookkeeping. `.github/workflows/workflow-conformance.yml` stays under the inherited `.github/`
exclusion (the Workflow Manager owns it). `scripts/`, `.claude/commands/` and `.workflow-manager/`
keep their inherited exclusion. This milestone never edits this repository's installed Workflow.

## Verification

- **Per checkpoint.** Run the named test modules, then the full sharded run
  (`python3 tools/run_tests.py`) before each checkpoint commit.
  - `PYTHONPATH=.` is never set.
  - `FORCE_COLOR` is unset.
  - The suite runs in the foreground (a backgrounded `&` breaks the Ctrl-C tests).
- **Inside a Controller-launched worker.** The runs go through a reaping-subreaper wrapper, as in
  C1b, and the checkpoint notes say so. A 1.4.0 driving Controller leaves orphans that the
  orphan-reap tests would otherwise count.
- **Red runs.** CP1 and CP2 each record their widened regression failing against the base code:
  CP1 against `93b82de`'s `controller/worker.py`, CP2 against the base's helpers.
- **Stress, informative only.** Each fixed test class is run as 6 parallel copies on 2 CPUs
  (`taskset -c 0,1`), in 5 rounds, and the result is recorded. The widened regressions are the
  proof.
- **CI.** The milestone's Draft PR must be green before functional review, and review rounds read
  the PR's checks first.
- **Functional review, the re-run flow.** This needs the operator's authorization, because it
  pushes a throwaway branch and PR, closed unmerged. On it, a probe test fails when
  `GITHUB_RUN_ATTEMPT` is 1 and passes otherwise.
  1. The first run is red.
  2. "Re-run failed jobs" makes `tests-result` green.
  3. The summary lists the superseded attempt-1 failure.
  4. A second probe that always fails stays red after a re-run of failed jobs.
  5. A third probe leaks a process (a `setsid sleep` it never ends) only when
     `GITHUB_RUN_ATTEMPT` is 1: attempt 1's `tests (<i>)` job and `tests-result` are red with a
     `LEAKED` verdict, and "Re-run failed jobs" re-runs that shard and turns `tests-result` green,
     listing the superseded leak (I8, Revision 2).

  Step 2 also confirms I4a's reliance on GitHub (Revision 3): after "Re-run failed jobs",
  `needs.tests.result` reads `success` in the new attempt, so `tests-result` can go green.

## Migration / data-integrity notes

- **Job records.** No field is added, and none is removed. A 1.4.2 record may carry a filled
  `cmdline` where 1.4.1 kept `""`. It may also carry one more `worker_state` publication, and so
  one more `event_seq` step. A 1.4.1 Controller re-attaching reads the filled text unchanged, and
  the two versions can re-attach to each other's jobs.
- **Shard records.** Schema version 2 adds `run_attempt`, and the 1.4.2 tools still read version 1
  (I9). A 1.4.1 checkout cannot read version 2 records; this matters only for a local `--replay`
  across versions.
- **CI artifacts.** They are renamed: `results-<i>-attempt-<a>` and `timings-ci-attempt-<a>`. The
  required checks and their names are unchanged.
- **CI behaviour.** A test that leaks a process now turns CI red, its shard's job included. The
  baseline has none.
