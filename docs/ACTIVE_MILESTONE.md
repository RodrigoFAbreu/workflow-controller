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
| CP2 Reaping every finished child in every state | Not started | |
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
