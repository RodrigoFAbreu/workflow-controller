# Active Milestone

## Milestone

`workflow-controller-worker-lifecycle-ownership`: controller worker lifecycle ownership (waiting
workers, owned background work, and restart-safe supervision). `docs/ROADMAP.md` section 1.4,
one urgent correctness hotfix.

- Plan: `docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md`, revision 16 (amendment 0
  resolved), approved at `c605d4c` (`EXTERNAL_APPROVE`).
- Registry: `docs/ai-workflow/registry/workflow-controller-worker-lifecycle-ownership-registry.json`
  (CP1-CP8).
- Governing workflow version: `2.2`. Base commit: `4280bb6`.
- Ground truth for phase and checkpoint status: `docs/ai-workflow/WORKFLOW_STATE.json`.

The previous milestone, `workflow-controller-trunk-branch-pr-release-orchestration`, is complete;
its narrative is archived at
`docs/milestones/completed/workflow-controller-trunk-branch-pr-release-orchestration.md`.

## Goal

A worker's lifecycle action is terminal only when the action has really finished, not when a
harness turn ends. The Controller keeps one `claude` session alive while the worker owns
background work (tasks, Monitors, wakeups, background subagents), ends it only at a quiescent
terminal turn, and reconciles only after the worker has ended and its owned work has drained.

## Checkpoint progress

| id | status | commit |
| --- | --- | --- |
| CP1 -- harness contract evidence and fake harness | complete | `ef07f28` |
| CP2 -- worker stream state machine | complete | `ed02157` |
| CP3 -- streaming-input launch and supervision | complete | this checkpoint's commit |
| CP4 -- job lifecycle integration | not started | |
| CP5 -- restart recovery | not started | |
| CP6 -- same-phase durable progress | not started | |
| CP7 -- operator diagnostics | not started | |
| CP8 -- documentation and full verification | not started | |

### CP1 -- harness contract evidence and fake harness (complete)

- **Fixtures adopted.** `tests/harness_contract/`: the 18 amendment-0 fixtures (seven imported
  job streams; P1-P6 and P8-P11) adopted byte for byte, plus P12's two new ones. `README.md`
  names each fixture's source and records the SHA-256 of all 40 files; a contract test checks
  them.
- **P12 gate passed.** `capture.py` gained only P12's two probe definitions (exact named
  arguments, `WAITING-ALPHA`/`PROBE-DONE`/`PROBE-EXTRA`, `"linger": 200`). Both captures were
  `ADOPT` on the first attempt (no recapture) and both `gate_deviations` lists are empty:
  `cancelledWakeups` is `0` for a stop inside the only wakeup's fire turn and `1` when the fire
  turn first schedules BRAVO, exactly the settlement rule's assumption. No plan amendment is
  triggered; CP2 may start.
- **`tests/harness_contract/p12_admission.py`.** `admit` judges only the model's actions up to the
  cut point (sequence complete, or an exact named call answered `is_error`), with the
  `PROBE-EXTRA`-only excuse; `gate_deviations` reports every departure from the pinned P12
  contract.
- **Fake harness.** `tests/fake_claude.py` gains a streaming-input mode (`--input-format
  stream-json`): the task is read from the stream-json user message (`_task()` falls back to it),
  existing variables play as one turn and then wait for stdin EOF (or exit at once with no
  `result`), tool processes inherit no descriptor beyond 0-2, and `FAKE_CLAUDE_TURNS` scripts
  turns (`text`, `thinking`, `tool`, `tools`, `bash_bg` with real tagged processes and orphan
  modes, `task_stop`, `await`, `monitor`, `wakeup`/`wakeup_stop` with the measured bracket and
  the fake's own `cancelledWakeups` count, `lifecycle_fault`, `subagent_handback`, `commit`,
  `write`, `end_turn`), with the P1 kill sequence at stdin EOF.
- **Contract tests.** `tests/test_fake_claude_contract.py` (65 tests): digests; the recogniser's
  evidence from the fixtures alone; the P12 fixtures' admission and gate; every P12 admission case
  the plan lists, built in memory from `p5_p11_wakeup_fires`; `admit`'s independence from the
  harness; each fixture replayed through the fake reproduces its projected event sequence (19 of
  20; `job_2857a730_two_results` is outside the fake's model, see the README); the fake's own
  streaming behaviours; the teardown guarantee.
- **Teardown guarantee.** `tests/process_fixtures.py` gains `reap_recorded_workers(runtime_root)`
  (identity-checked `SIGKILL` of recorded `worker_process`, `worker_anchor`,
  `worker_state.owned_processes` and `ownership_tag`-tagged processes) and
  `ReapRecordedWorkersMixin`, for the streaming-worker tests CP3 onward adds.
- **Opt-in live probe.** `tests/test_integration_disposable_repo.py`'s
  `LiveHarnessContractProbeTest` (`CONTROLLER_LIVE_WORKER=1`) re-runs P3-P6 and P8-P11 into a
  scratch directory and checks the harness sequences and the three bracket facts; its P12 method
  is CP8's final re-measurement. Not run live in CP1.
- **Deferred to CP2 by construction.** The assertion that runs B over each fixture with and
  without the dropped kinds needs B (`controller/worker_stream.py`), which CP2 builds.
- **CI placement.** `tools/ci_workflows.py` places `test_fake_claude_contract` in the `worker`
  shard, and `.github/workflows/validate.yml` is regenerated by `--write` (the placement test
  requires every test module to be placed exactly once).
- **Verification.** `python3 -m unittest tests.test_fake_claude_contract`: 65 tests OK.
  Full suite `python3 -m unittest discover -s tests -t .`: 1667 tests, `OK (skipped=8)`, 181 s
  (baseline 1600, `skipped=6`; the two new skips are the opt-in live probe).

### CP2 -- worker stream state machine (complete)

- **`controller/worker_stream.py` (new, pure).** `WorkerStream` consumes stdout bytes in any
  chunking (complete lines only; `finish()` takes a final unterminated line at EOF) and tracks
  turns and their open times, every `result`, owned tasks (listed and not terminal), wakeups
  (`pending` -> `fire_matched` -> `settled`, due time harness-stated `in Ns` or the 60-3600 s
  clamp), and `command_lifecycle` brackets correlated by `command_uuid` only: conditions 1-6,
  the provisional match for a stop inside a bracket, and the eleven sticky anomaly kinds
  (including `wakeup_count_mismatch`). `settle_wakeups` applies the `settled_wakeups` fact
  (only a `fire_matched` wakeup settles; anything else is recorded as ignored).
  `owned_work()`/`quiescent()` are as B defines them. No clock is read and no `origin` is
  consulted.
- **Terminal classification.** `classify(data, mode=, facts=SupervisorFacts(...), returncode=)`
  applies B's nine ordered rows and returns `(outcome, terminal_result, stream_diagnosis)`
  (reason, `secondary_reasons`, results with `queued_turn_count`/`terminal_reason`/`origin`,
  `tasks_seen` with before/after-the-end, `wakeups_seen` with how each settled, `wakeup_stops`,
  `command_lifecycles`, `command_lifecycle_anomalies`, malformed lines, unknown events, ignored
  settlements). `returncode=None` is an unknown exit status (a re-attach) and skips rows 1-2.
- **`controller/worker.py`.** `_parse_worker_stream`/`_classify` are removed; `launch` classifies
  with `mode="print"` (until CP3), fills `WorkerResult` from the last `result`, and carries the
  new optional `WorkerResult.stream_diagnosis`. Behaviour changes for print-mode launches, by
  design: several `result` events are accepted (the last is used), and non-structural events
  after the final `result` no longer make a clean run `AMBIGUOUS`. `controller/__init__.py` and
  the package's dependency order place `worker_stream` before `worker`.
- **Tests.** `tests/test_worker_stream.py` (new, 68 tests) covers CP2's list: every CP1 fixture
  classifies as B's table says (the six failures `owned_work_killed_at_exit` with their killed
  tasks, `2857a730` `SUCCESS` with two results, `5d4a976a`'s Monitor stop before the end, P3/P4
  `SUCCESS` after waiting, P5's wakeups matched only at `completed(X)` and still owned, P9 and
  both P12 fixtures settled by stop with agreeing counts), and CP1's deferred assertion (B's
  state and classification are identical with and without the six dropped kinds, over every
  fixture). It also covers row precedence, positional kills, wakeup time sources, no clock,
  origins never evidence, the bracket edits of P5, round 9's wrong match and its variants,
  settlement and counts, task openness, `queued_turn_count`, and incremental parsing at any
  chunking. `tests/test_worker.py`'s stream tests are rewritten against `worker_stream`: "two
  result events" is now `SUCCESS`, and an unknown exit status is no longer read as a signal.
  `tools/ci_workflows.py` places the new module in the `worker` shard (`validate.yml`
  regenerated).
- **Verification.** See the checkpoint commit for the exact commands and results.

### CP3 -- streaming-input launch and supervision (complete)

- **Streaming launch (A).** `worker.launch` runs `claude -p --input-format stream-json
  --output-format stream-json --verbose --permission-mode <m> [--model] [--effort]
  --append-system-prompt WORKER_LIFECYCLE_NOTE [--disallowedTools <list>]` (`worker_argv`, equal
  to CP1's captured production argv) and writes the task as one stream-json user line. The pipe's
  write end belongs to the new stdin anchor, `controller/anchor.py` (stdlib only, run from its
  source text read once at import, `-I -c`, own session, empty environment, only the pipe and
  `pass_fds`): `SIGUSR1` closes stdin; it ends itself once the worker is gone, no same-uid
  process carries the tag and no supervisor holds the supervisor lock, for
  `ANCHOR_ORPHAN_SECONDS`. `on_spawn(worker, anchor=, ownership_tag=)`; the tag is appended to
  `WORKFLOW_CONTROLLER_OWNERSHIP`; `routing.ASYNC_UNOWNABLE_TOOLS` (`CronCreate`, `CronDelete`,
  `RemoteTrigger`) ends every route's disallow list.
- **Supervision (C).** `RUNNING`/`WAITING`/`ENDING`/`DRAINING`/`ENDED` through
  `on_state_change(state, details)` (tasks, wakeups with due or settle time, open brackets, the
  pruned `owned_processes`, `owned_processes_seen_count`, `excluded_processes`, scan status;
  `ending_offset` and the supervisor facts at `ENDING`). `ENDING` only at a quiescent turn that
  stays quiescent with no new bytes for 0.1 s, or 1.0 s when a background task ended during or
  after the last turn (in the CP1 fixtures the harness opens that task's already-queued
  notification turn 4-52 ms after a `queued_turn_count: 0` result), or at a breach: an overdue `pending`
  wakeup over task-free idle time, or a bracket stalled with no task open (the stalled
  bracket's `command_uuid` is a new `SupervisorFacts` field). `fire_matched` wakeups settle
  after `WAKEUP_SETTLE_SECONDS` of idle time (no turn, task or bracket), restarted by any turn,
  task or bracket. Ownership: the worker's group, the tag, adoption as a child subreaper
  (`prctl`, restored after), and every process already seen owned while its start ticks
  match; recognised daemons (`RECOGNISED_DAEMONS`) are excluded. Only adopted zombies are
  reaped, by pid. The drain is bounded by `DRAIN_DETACH_SECONDS` and returns `DrainDetached`,
  ending nothing; `--timeout` ends the group, the owned processes and the anchor.
- **Job (CP3's share).** `execute_step` takes `jobs/<job_id>/supervisor.lock`
  (`runtime.open_lock_file`) before the pre-spawn `LAUNCHED` write and releases it after the
  record's last write; that write now carries `ownership_tag` (the job id) and
  `worker_state: STARTING`, and the `on_spawn` flush adds `worker_anchor`. A `DrainDetached`
  return is exit 45 (`LifecycleWorkerActiveError`) with the record `LAUNCHED`, until CP4.
- **Fake.** `tests/fake_claude.py`: a `bash_bg` step may name its own `command`, and at stdin
  EOF the diagnostic file gains `stdin_eof_at`/`stdin_lines_after_task` (`stdin_at_eof` keeps its
  CP1 meaning).
- **Tests.** `tests/test_worker.py` gains 41 CP3 tests (the anchor; every supervision, wakeup,
  bracket, wrong-match and ownership case of CP3's list, the double-breach residue pinned; the
  quiescence windows pinned to the fixtures' measured gaps) and
  rewrites the argv, `stdin_at_eof` and `setsid` tests; `tests/test_job.py` gains the
  supervisor-lock scope test and rewrites the argv/disallow-list, schema-field and setsid-lock
  tests. Rewritten beyond the plan's list, because they pinned the same behaviour: the task in
  `argv[1]` (`tests/test_cli.py`, `tests/test_integration_disposable_repo.py`), the no-policy
  golden (`tests/golden/no_policy_lifecycle.json`: only the three additive record fields and the
  streaming argv moved), and five Controller-loss tests whose released orphan was expected to
  exit by itself (`tests/test_resume.py`'s interruption and orphan tests,
  `tests/test_lifecycle_orchestration.py`'s orphaned apply worker): the orphan's anchor keeps
  stdin open until CP5's re-attach, so each now ends the recorded anchor
  (`process_fixtures.end_recorded_anchor`) and keeps its reconciliation assertions.
- **Verification.** See the checkpoint commit for the exact commands and results.

## Next action

`/milestone-implement workflow-controller-worker-lifecycle-ownership` for CP4.
