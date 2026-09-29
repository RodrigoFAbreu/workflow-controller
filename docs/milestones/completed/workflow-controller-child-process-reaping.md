# Archived milestone narrative — `workflow-controller-child-process-reaping`

Archived verbatim from `docs/ACTIVE_MILESTONE.md` on 2026-09-30, as part of
`/accept-milestone`'s own step 5, at this work item's own acceptance. This
file's content below this notice is unedited from the version
`docs/ACTIVE_MILESTONE.md` carried immediately before this acceptance
(functional review round 1, implementation revision 2: checklist evidence
commit `56aa479f9d4a1f25adb2e8c28e207389c2649868`, checklist blob
`809afca9ce7e510e284c925e0e9df34dadf64343`; the deferred follow-ups and the
post-acceptance release steps are listed in `docs/ACTIVE_MILESTONE.md`'s
completion status, not here).

**Not archived here, deliberately** (same reasoning the prior archived
milestones' own files already state for their own milestones):
`docs/ai-workflow/CONTROLLER_CHILD_PROCESS_REAPING_PLAN.md`, its registry
(`docs/ai-workflow/registry/workflow-controller-child-process-reaping-registry.json`),
and its requirements mapping
(`docs/ai-workflow/requirements/workflow-controller-child-process-reaping-mapping.json`)
all remain at their original paths, unmoved and unmodified —
`docs/ai-workflow/WORKFLOW_STATE.json`'s own
`work_items["workflow-controller-child-process-reaping"]` entry still
declares these exact paths as its `plan_path`/`registry_path`/`mapping_path`,
and `plan_approval.review_content_manifest` pins their blobs at these same
paths, so moving any of them would make that historical approval record's own
manifest unresolvable. The `workflow-controller-child-process-reaping`
entry in `docs/ai-workflow/WORKFLOW_STATE.json` (`work_items` map, phase
`MILESTONE_COMPLETE`) and the full Git history of its approvals are likewise
untouched by this archival.

---

# Active Milestone

## Status

**Functional review.** Implementation revision 2 has technical approval (`987017d`); the checklist is
below. `workflow-controller-child-process-reaping` (`docs/ROADMAP.md` step C1b,
section 11.1.1 "Reaping every child process"). The plan is
`docs/ai-workflow/CONTROLLER_CHILD_PROCESS_REAPING_PLAN.md`, revision 8, approved at `e074e3b`
(`EXTERNAL_APPROVE`). The base commit is `6a24f64`. Governing workflow version `2.2`, lifecycle
authority Workflow 2.6.0, driving Controller the installed 1.4.0. Pull request title
`fix: reap every finished child process the Controller holds as a subreaper` (1.4.1).

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-squash-merge-tag-versioning.md`.

## Goal

The Controller collects every finished child it holds as a subreaper, on every supervision tick,
in every worker state, each by its own pid, never the worker, its anchor or a child the process
waits for; a background reaper covers the gaps between launches. A regression test bounds the
zombies under the Controller while a fake worker orphans hundreds of processes. The tests'
throwaway Git repositories never run automatic maintenance. Ownership and published worker state
are unchanged (I3).

## Checkpoint progress

| Checkpoint | Status | Notes |
|---|---|---|
| CP1 Test hygiene: throwaway repositories without automatic maintenance | Complete | See below |
| CP2 Reaping every finished child in every state | Complete | See below |
| CP3 Documentation and full verification | Complete | See below |

### CP1 -- test hygiene: throwaway repositories without automatic maintenance

- `tests/fixtures.py`: `QUIET_MAINTENANCE` (`maintenance.auto=false`, `gc.auto=0`),
  `git_init(path, *options, cwd=None)` (init, then `git -C path config` for each key, bare
  repositories too) and `git_clone(source, dest, *options, cwd=None)` (`clone -c` for each key).
- Every repository-creating site in `tests/` goes through them: 34 sites, as the guard counted
  them -- `fixtures.py` (6, including `build_origin_pair`'s two), `process_fixtures.py`,
  `test_gitrepo.py` (2), `test_integration_disposable_repo.py` (3), `test_job.py`,
  `test_managed_repo.py`, `test_milestone_branch.py` (2), `test_packaged_runtime.py` (2),
  `test_pull_request_lifecycle.py` (2), `test_release_tools.py`, `test_release_txn.py`,
  `test_runtime.py` (2, the `os.system` shell strings), `test_trunk_orchestration_e2e.py` (3),
  `test_trunk_preflight.py` (2), `test_workflow_contract.py` (2) and
  `test_workflow_release_migration.py` (3). This matches the plan's measured list.
- `tests/test_fixtures_git_hygiene.py` (new): the AST guard with the plan's three rules (token
  sequences, `git`/`_git`/`*_git` helpers, non-docstring shell strings), skipping exactly the
  `tests/workflow_releases/` prefix and allowing only the bodies of `fixtures.git_init` and
  `fixtures.git_clone`; its self-tests on synthetic sources (the plan's flagged and unflagged
  forms, and the prefix cases); and a read-back test that creates a repository with `git_init`,
  a bare one and a clone, with `GIT_CONFIG_*` cleared, and reads both keys back with
  `git config --local --get`. The self-tests write `GIT` for `git` in their shell-form sources,
  so the guard's own literals do not match the shell rule.
- Nothing outside `tests/` changed (apart from this file and the state file), and nothing under
  `tests/workflow_releases/`.

Verification: `tests.test_fixtures_git_hygiene` passes. The full sharded run
(`python3 tools/run_tests.py`, 2418 tests, 5 shards) passed with no failures. It ran inside a
Controller-launched worker, so it went through a reaping-subreaper wrapper
(`PR_SET_CHILD_SUBREAPER` + `waitpid(-1)` loop), with `FORCE_COLOR` and `PYTHONPATH` unset.

### CP2 -- reaping every finished child in every state

- `controller/worker.py`:
  - `_ProcStat.session` (field 6), declared before `ppid`.
  - `_direct_children(pid=None)`, read from every thread's `task/<tid>/children`. It returns
    `None` when the file does not exist.
  - The sweep, `_collect_children(exclude, baseline, *, force_full=False)`. Without `children`
    files it falls back to the full `/proc` scan, at most once per `_OWNERSHIP_SCAN_SECONDS`
    unless `force_full`.
  - `_FOREIGN_BORN`, kept by `_update_foreign_born`. The walk only goes through same-session
    descendants, admits a descendant only when its `ppid` is the walked parent, and prunes
    entries that are gone or reused.
  - One predicate, `_reap_excluded`, covers `_LAUNCH_CHILDREN`, a same session outside
    `_FOREIGN_BORN`, and `_REAP_EXCLUDED`. It is applied at recording by `_record_adopted`. That
    is now the only writer of `_ADOPTED`, used by the sweep, `_Ownership.scan` and
    `_reap_killed_children`. `_reap_adopted` applies it again, to a fresh `stat`, before each
    `waitpid`.
  - `_SPAWN_LOCK` (`RLock`) is held by every record-and-reap and across the worker's and the
    anchor's `Popen` plus their registration.
  - `exclude_from_reaping(spawn)` and `reap_adopted_children()`.
  - `_LaunchExclusions` holds the baseline, the worker's pid (then its start ticks) and the
    anchor. Its `final_sweep` is registered on the `ExitStack` before `_child_subreaper()`.
  - `_Supervision._collect()` runs on every loop iteration and in the drain loop (in place of
    the bare `_reap_adopted()`), and in `_finish`, `_detach` and `_interrupt` with
    `force_full=True`.
  - The between-launch reaper, `workflow-controller-reaper`, starts through
    `_ensure_idle_reaper`. Its exit is atomic under `_SPAWN_LOCK`.
  - `_stop_idle_reaper` and `_reset_reaping_for_tests` are test support.
- Two choices the plan does not spell out:
  - `_reap_adopted` also drops a record whose pid now shows different start ticks, with no
    `waitpid`. This only narrows what it reaps (I1, I6).
  - `reattach`'s supervision has no sweep (`reaping=None`). It adopted nothing, and the plan
    changes nothing in `resume`/`reattach`.
- `controller/cli.py`: `cmd_run` reaps after every `_run_one_step`. `cmd_run` (its loop is now
  `_run_steps`) and `cmd_step` also reap in a `finally`.
- `tests/fake_claude.py`: the `orphan_burst` step and the same burst as `fake_claude.py
  --orphan-burst`. Each grandchild calls `setsid` and exits. The call returns once the last
  grandchild has exited (a pipe held by each). There is also a `detach` variant, which waits
  for the fake to exit, and a `gate` file. `tests/test_fake_claude_contract.py` covers them.
  One test checks, under a subreaper, that every grandchild is a session leader re-parented to
  it.
- `tests/fixtures.py`: `isolate_idle_reaper(case)`.
- `tests/test_worker.py`:
  - `ChildReapingTest`: the three per-state regressions, with bound 30 and at most 5 samples
    above it, at least 75 samples during the burst, and the burst started through a gate file
    once the state is published.
  - Seam-level tests: `ReapingSweepTest`, `ReapingPredicateTest`, `ForeignBornTest`,
    `IdleReaperTest` (including the exit handshake and its mirror) and `ReapingResetTest`
    (three cases, real children).
  - `ReapingLaunchTest`: waited-for statuses (45 across 20 or more sweeps, and under
    `exclude_from_reaping`), overlapping launches (exit 3 and 5, neither records the other's
    worker or anchor), the spawn-to-registration window for `exclude_from_reaping` and for
    `launch`'s worker and anchor, the same-session grandchild, the between-launch orphan, the
    final-sweep ordering on the normal, `DrainDetached` and exception paths plus the
    failed-capture case, and the published sequence with and without the sweep.
  - `FollowerStartsNoProcessTest` (I6).
- `tests/test_cli.py` `ChildReapingCliTest`:
  - `run` reaps after the step that left a zombie. It stands in for `launch` at
    `job.execute_step`, running the real `_child_subreaper()` and final sweep.
  - `step` reaps even when the step raises.
  - The paused-boundary test. The event fires only on an entry at which the pause file exists,
    and that entry is number 2.

Evidence:

- **Red run.** `ChildReapingTest` was run against `6a24f64`'s `controller/worker.py`, plus a
  two-name shim so the isolation fixture imports. `RUNNING` failed with 118 samples above 30
  (peak 149), and `WAITING` failed with 118 samples above 30 (peak 150).
- **`DRAINING` passed there, which the plan did not expect.** Once the worker's group is
  empty, 1.4.0's drain loop rescans ownership (`empty or ... next_scan`), and so records every
  0.2 s rather than every 1 s. The burst runs from a `setsid` descendant, so the group is
  empty.
- **At the head.** The peak was 11 in `DRAINING` and 10 in `RUNNING` and `WAITING`, with no
  sample above 30 and about 169 samples per run.
- **Explicit-exclusions audit.** The grep for `start_new_session=True`, `os.setsid` and
  `preexec_fn` was run over `tests/`, outside `tests/workflow_releases/` and `fake_claude.py`.
  No site both runs during a launch in the same process and reads its child's exit status:
  - `process_fixtures` `spawn_sleeper`: its status is never read.
  - The zombie builders run in a child interpreter.
  - `test_worker` ~1094, ~1164 and ~1811, `test_observe` ~385, `test_cli` ~2177 and
    `test_resume` ~2192 are waited for, or killed, outside any launch.
  - `test_resume` ~2849 is used around `resume`, which never launches.
  - `test_lock`, `test_run_tests`, `test_test_shards` and `test_fake_claude_contract` have no
    launch.
  - `harness_contract/capture.py` and `test_integration_disposable_repo` ~2121 run the real
    CLI, and the child is killed.

  So nothing needed `exclude_from_reaping`. The new tests' own new-session children are either
  wrapped or never waited for.
- **Golden generators with `--check`.** `generate_external_implementation_review_decisions` and
  `generate_no_policy_lifecycle` are current, and so is `generate_plan_stage_decisions --release
  2.6.0`. The default (2.5.1) file reports the documented permitted difference (the
  `AMENDING_PLAN` reason). The same happens at `3abf9d1`, and
  `tests.test_golden_plan_stage_decisions` accepts it.

Verification: the full sharded run (`python3 tools/run_tests.py`, 2464 tests, 5 shards) passed
with no failures. It ran through the reaping-subreaper wrapper, since this is a
Controller-launched worker, with `PYTHONPATH` and `FORCE_COLOR` unset. A first run with
`FORCE_COLOR=3` failed only the known ANSI stderr match in
`tests.test_evidence.ContentDriftedRealTest`.

### CP3 -- documentation and full verification

- `docs/guide/workers.md`: a "Collecting finished children" paragraph under "Owned processes, the
  daemon list and the drain bound". It covers the per-tick collection by pid in every state,
  that collecting is not owning, the final sweep and the between-launch reaper, the same-session
  rule, and `worker.exclude_from_reaping(spawn)` for an embedder's new-session children (I6).
- `docs/adr/0004-worker-lifecycle-ownership.md`: a dated "Amendment (1.4.1)" note after the
  "reaps only its own adopted zombies by specific pid" paragraph. The decision is unchanged.
- `docs/guide/development.md`: a "Throwaway Git repositories" section (`fixtures.git_init` and
  `fixtures.git_clone`, the guard test, and why).
- `tools/test_timings.json` is unchanged: `CommittedTimingsTest` passes, since CP1 and CP2 only
  added classes.
- **Unchanged-path check.** `git diff 6a24f64 -- .workflow-controller/ pyproject.toml setup.py
  .github/` is empty.
- **Orphan measurement.** Each suite ran under a counting subreaper wrapper (`/tmp/count_reap.py`).
  The wrapper marks itself `PR_SET_CHILD_SUBREAPER`, reaps each orphan re-parented to it by pid
  after a `WNOWAIT` peek, and classifies it by the command line a 5 ms sampler saw (or `comm` for
  one that was already a zombie). `GIT_CONFIG_*` (the lanes' exports), `PYTHONPATH` and
  `FORCE_COLOR` were cleared, and SIGINT was reset to default before exec. The base run used a
  local clone checked out at `6a24f64`. Orphans re-parented to the wrapper:

  | Selection | `6a24f64` | head (`e68b917` + CP3) |
  |---|---|---|
  | full (`python3 tools/run_tests.py`) | 10,570 (10,491 Git; 118 seen as `git maintenance run`) | 3,572 (3,484 Git; 49 `git maintenance run`) |
  | `tests` (this repository's unittest suite) | 7,082 (7,005 Git; 76 `git maintenance run`) | 87 (1 Git; 0 maintenance) |
  | `conformance` (the seven frozen Workflow suites) | not run separately | 3,488 (3,486 Git; 36 `git maintenance run`) |

  Most Git orphans show only as `[git]` because they were already zombies when first sampled.
  This repository's own suite meets the plan's expectation: from thousands to a few dozen, and no
  Git maintenance. The 87 left are the tests' own deliberate orphans (`fake_claude.py`, the
  orphan bursts, the `wlo-cp1-*` fixtures and `sleep`/`bash`). The remaining Git orphans all come
  from the vendored Workflow conformance suites (`scripts/*_test.py`, run as `conformance:`
  atoms). Their fixtures create repositories without the keys. They are the installed Workflow's
  code, which this milestone never edits (plan, "Artifact declaration"), and CP1's guard covers
  only `tests/`. Under a 1.4.1 Controller those orphans are collected on every tick anyway, so
  the fix covers them. The hygiene only lowers the count.
- **Full verification.** The head measurement run is also the full sharded suite: 2464 tests,
  5 shards, `coverage: exact`, all PASS, wall time 179.1 s. It ran through the counting
  (reaping) subreaper wrapper, since this is a Controller-launched worker, with `PYTHONPATH`,
  `FORCE_COLOR` and `GIT_CONFIG_*` unset. The base run (2411 tests) also passed.

## Pull request body (1.4.1 release notes)

From 1.4.0 the pull request body is the release notes (`docs/README.md`). The Draft PR for this
milestone carries:

> **Reap every finished child process the Controller holds as a subreaper (1.4.1)**
>
> **The leak.** While it supervises a worker, the Controller marks itself a child subreaper, so
> every process the worker orphans is re-parented to it. 1.4.0 recorded those children only when
> an ownership scan ran, and collected them only when the job drained or ended. A worker whose
> commands orphan many short-lived processes while `RUNNING` (Git's detached automatic
> maintenance, after every commit in a throwaway repository) therefore left them as zombies for
> the whole job. An orphan re-parented after the job's last scan was never collected before the
> Controller exited. On 2026-09-29 two long-running Controllers filled the per-user process limit
> this way, and every Claude Code process on the host aborted.
>
> **The fix.** The Controller now collects every finished child it holds on every supervision
> tick, in every worker state (`STARTING`, `RUNNING`, `WAITING`, `ENDING`, `DRAINING`). It
> reads `/proc/<pid>/task/<tid>/children`, with a rate-limited full `/proc` fallback, and reaps
> each child by its own pid, never `waitpid(-1)`. It collects once more after a launch gives up
> the subreaper, and between launches a background reaper thread keeps collecting the recorded
> children still running, until none is left. It never collects the worker, its anchor, or a
> child the process spawned in its own session, so an embedder's own subprocesses keep their
> exit statuses. An embedder that starts a child in a new session during a launch, and reads its
> status, wraps the spawn in `worker.exclude_from_reaping(spawn)`. Ownership, what the job waits
> for, and the published `worker_state` are unchanged. Collecting a zombie is not owning it, and
> job and run records keep 1.4.0's format.
>
> **Test hygiene.** Every throwaway Git repository the tests create goes through
> `fixtures.git_init`/`fixtures.git_clone`, which write `maintenance.auto=false` and `gc.auto=0`
> into its config. A guard test keeps it that way. Orphans from this repository's unittest suite
> fell from 7,082 to 87, none of them Git maintenance.
>
> **Operator step.** Install 1.4.1 between Workflow Manager milestones (the shared lane plan),
> then drop the `--max-steps 1` stopgap from both lanes' scripts. The stopgap keeps working with
> 1.4.1 until then.

## Functional review checklist

Round 1, implementation revision 2 (technical approval `987017d`, reviewed head `be4b9f2`). The
expected results below were measured on 2026-09-30 against `987017d`.

### Setup

- A scratch directory outside the repository, here `$S` (for example `/tmp/c1b-fr`). Nothing in
  these flows writes to the repository, its remote, GitHub or the installed Controller (1.4.0).
- In every shell: `export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=maintenance.auto GIT_CONFIG_VALUE_0=false GIT_CONFIG_KEY_1=gc.auto GIT_CONFIG_VALUE_1=0`,
  except where flow C says to clear it.
- A scratch bare origin with the real tags, `origin/main` (`6a24f64`) and the milestone branch:
  ```bash
  R=/home/rodrigo/Workspace/workflow-controller; S=/tmp/c1b-fr; mkdir -p "$S"
  git init -q --bare "$S/origin.git"
  git --git-dir="$S/origin.git" fetch -q --no-tags "$R" "+refs/remotes/origin/main:refs/heads/main" \
    "+refs/tags/v*:refs/tags/v*" \
    "+refs/heads/milestone/workflow-controller-child-process-reaping:refs/heads/milestone/workflow-controller-child-process-reaping"
  ```
- A local build of the milestone head in a throwaway virtual environment:
  `git clone -q "$S/origin.git" "$S/src" && git -C "$S/src" checkout -q --detach origin/milestone/workflow-controller-child-process-reaping && python3 -m venv "$S/venv" && "$S/venv/bin/pip" -q install "$S/src"`.
- Test runs from this session need a reaping subreaper wrapper around them, because a
  Controller-launched worker's orphans otherwise stay zombies under it. Unset `FORCE_COLOR`, and
  never set `PYTHONPATH=.`.

### Flows

**A. The local build's version.** Run `"$S/venv/bin/workflow-controller" --version`. Expected:
`workflow-controller 1.4.0` and `runtime: package (local build from 987017da9acc)`. The highest
reachable release tag is `v1.4.0`.

**B. Nothing else changed.** In the repository, run `inspect .` and `explain .` with the installed
1.4.0 and with `"$S/venv/bin/workflow-controller"`. Expected: all exit 0, and the outputs are
byte-identical. `git diff 6a24f64 -- .workflow-controller/ pyproject.toml setup.py .github/`
is empty.

**C. The leak is gone (the per-state regressions).** Run
`python3 -m unittest tests.test_worker.ChildReapingTest` at the head, under the reaping wrapper.
Expected: 3 tests OK (`RUNNING`, `WAITING`, `DRAINING`). Each launches a real supervised worker (the
fake `claude`) that orphans a burst of short-lived processes in that state, and samples the zombies
held by the supervising process. None may exceed 30 for more than 5 samples. CP2 recorded a peak
of 10 or 11 at the head, against a peak of about 150 (118 samples above 30) for `RUNNING` and
`WAITING` at `6a24f64`.

**D. The safety tests.** Run `tests.test_worker.ReapingSweepTest ReapingPredicateTest
ForeignBornTest IdleReaperTest ReapingResetTest ReapingLaunchTest FollowerStartsNoProcessTest`,
`tests.test_cli.ChildReapingCliTest`, `tests.test_fixtures_git_hygiene` and
`tests.test_fake_claude_contract`, together with C. Expected: `Ran 119 tests`, `OK`, about 46 s.
They cover:
- waited-for statuses kept (`Popen` children, `exclude_from_reaping`);
- the worker and the anchor never collected;
- overlapping launches, including the reused-pid exclusion from the external review;
- the same-session grandchild;
- the between-launch reaper and the paused step boundary;
- `run`/`step` reaping after a step and on an exception;
- the git-hygiene guard.

**E. Fewer orphans from the test suite.** Run this repository's unittest selection
(`python3 tools/run_tests.py --select tests`, or the `tests` part of the plan) under a counting
subreaper wrapper. The wrapper marks itself `PR_SET_CHILD_SUBREAPER`, reaps each orphan
re-parented to it by pid, and counts them, with `GIT_CONFIG_*` cleared. Run it at the head and at
`6a24f64` (a clone checked out there). Expected: thousands at `6a24f64` (CP3 measured 7,082, of
which 7,005 were Git), and a few dozen at the head (CP3 measured 87, 1 Git, 0 `git maintenance`).
The vendored Workflow conformance suites still orphan Git processes. That code is not this
milestone's to change (it is out of scope), and 1.4.1 collects those orphans every tick.

**F. It releases 1.4.1.** In a clone of `$S/origin.git`, with the repository's `tests/fake_gh.py`
as `gh` (the releases `v1.1.1`, `v1.2.0`, `v1.2.1`, `v1.3.0` and `v1.4.0` published; the env vars
as in the C1 checklist), build the squash commit
`Q=$(git commit-tree "<milestone head>^{tree}" -p origin/main -m "fix: reap every finished child process the Controller holds as a subreaper (#11)")`,
advance `main` to it, check it out, and run:
- `python3 tools/release.py check-title "<that subject>"`. Expected: `ok: fix → patch`.
- `python3 tools/release.py version`. Expected: `1.4.0`.
- `python3 tools/release.py classify --commit "$Q"`. Expected:
  `ok: RELEASE_DUE: 1.4.1 has no tag and no release`, `version=1.4.1`, `tag=v1.4.1`, exit 0.

**G. PR #11.** Run `gh pr checks 11`. Expected: every check passes, including `PR title` (the
title is the plan's `fix: …`), `validate / tests-result` and `workflow-conformance`.

**H. Documentation.** Check each of these describes what C to F showed:
- `docs/guide/workers.md`, "Collecting finished children";
- `docs/adr/0004-worker-lifecycle-ownership.md`, "Amendment (1.4.1)";
- `docs/guide/development.md`, "Throwaway Git repositories";
- the PR body (the 1.4.1 release notes above), including the operator step: install 1.4.1 between
  Workflow Manager milestones, then drop `--max-steps 1`.

### Known limitations and out of scope

- No live Controller run on a real repository is part of this round. C launches real supervised
  workers through `launch`, which is the code the fix changes, and the lanes switch to 1.4.1 after
  the release.
- `reattach`'s supervision (`resume`) has no sweep. It adopted nothing, and the plan leaves
  `resume` unchanged.
- The full `/proc` fallback is rate-limited, so on a host without `task/<tid>/children` a zombie
  can wait up to one ownership-scan interval.
- The vendored Workflow conformance suites still create repositories with maintenance on (E).
- The known CI flakes (`OwnershipTest`, `CrossProcessEventSeqTest`) are C2.
