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
| CP2 The tests wait for the right signal | Not started | |
| CP3 A re-run of failed jobs counts | Not started | |
| CP4 A leaked process fails the run | Not started | |
| CP5 Documentation and full verification | Not started | |

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
