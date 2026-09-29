# Active Milestone

## Status

**Implementing.** `workflow-controller-child-process-reaping` (`docs/ROADMAP.md` step C1b,
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
| CP3 Documentation and full verification | Not started | |

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
