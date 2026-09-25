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
| CP3 -- streaming-input launch and supervision | complete | `e8364eb` |
| CP4 -- job lifecycle integration | complete | `e354f10` |
| CP5 -- restart recovery | complete | this checkpoint's commit |
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

### CP4 -- job lifecycle integration (complete)

- **Record (D).** `execute_step` passes `on_state_change` to `worker.launch`. Each call writes
  the still-`LAUNCHED` record (authoritative) with `worker_state` = `{state, since, turns,
  waiting_on, owned_processes, owned_processes_seen_count, excluded_processes, scan}` and, once
  the session was ended, the supervisor facts at the top level (`ending_offset`,
  `wakeup_overdue_declared_at`, `command_lifecycle_overdue_declared_at` with
  `command_lifecycle_overdue_command_uuid`, a non-empty `settled_wakeups`). Then it appends
  `worker_running`/`worker_waiting`/`worker_ending`/`worker_draining`/`worker_ended`, whose
  compact details carry `state_changed` (`false` for a flush of what the worker waits on or
  owns), the turn count, task ids, wakeup ids and states, bracket uuids, the owned-process
  count and `ending_offset`. `COMPLETED` is written only after `launch` returned (`ENDED`; a
  `launch` double's `STARTING` is completed to `ENDED`), and `worker` gains `stream_diagnosis`
  and `owned_processes_seen`. The status sequence stays `PLANNED -> LAUNCHED (x2+) ->
  COMPLETED -> FINISHED|FAILED`.
- **Drain detach.** A `DrainDetached` return writes the record `LAUNCHED` at `DRAINING` with
  `drain_detached_at`, appends `worker_drain_detached` (pids and command lines), both under the
  supervisor lock, then raises the new `OwnedWorkDetachedError` (a `LifecycleWorkerActiveError`,
  exit 45) naming the pids, their command lines and `resume`.
- **Validation case 3.** A `LAUNCHED`/`COMPLETED` record's `worker_state` must be one of
  `WORKER_STATES` (`ENDED` when `COMPLETED`); `ending_offset` only at `ENDING`/`DRAINING`/
  `ENDED`; the three `ENDING` facts need `ending_offset`; the bracket declaration names its
  `command_uuid`; `settled_wakeups` is a list of distinct non-empty strings.
- **Strings (O3).** `_OTHER_HOLDER_SENTENCE` and `controller/lock.py`'s docstring now name the
  recorded anchor (or a group member), never a stray descendant, as the other lock holder.
- **Fake.** A `FAKE_CLAUDE_SCRIPT` invocation may be `{"turns": [...]}` (a scripted streaming
  session per invocation), with an `actions` step performing the scripted actions inside a turn;
  the script's counter line gains `at`; `bash_bg`'s `orphan_write_file` records an orphan's end.
- **Tests.** `tests/test_lifecycle_orchestration.py`: R12a-c (`FINISHED` on the continuation
  turn, verification done before `COMPLETED`), R12d (anchor killed mid-wait: `AMBIGUOUS` then
  `FAILED`; the apply variant then gated by the relaunch bound), R13 (task and reparented-orphan
  variants; N+1 starts after N's background work; a second-process `step` exits 45), R14 (task,
  wakeup stop, settle window, irregular fire), R14b (`IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION`,
  the stop-inside count mismatch, a concurrent `step` during the window exits 45), and the lock
  lifetime (held while `WAITING` and by the anchor after the Controller is SIGKILLed).
  `tests/test_job.py`: worker_state persistence, the flush-failure and launch-double cases, the
  drain detach and the recognised daemon. `tests/test_job_validation.py`: the case-3 rules.
  Rewritten because they pinned exactly two `LAUNCHED` writes or the old event sequence or
  holder sentence: `tests/test_job.py` (write-sequence prefix, second-`LAUNCHED` delta,
  `COMPLETED` field set + `ending_offset`, supervisor-lock scope, the event-log and best-effort
  append tests, the holder sentence), `tests/test_job_validation.py` (two status sequences),
  `tests/test_lock.py` (the holder sentence). Beyond the plan's list, because the persisted
  stream byte offsets depend on each case's path length: `tests/test_observation_equivalence.py`
  normalises offset keys like pids and compares `worker` without its per-stream
  `stream_diagnosis` (asserting the same reason), and the no-policy golden's generator treats
  offsets as volatile; `tests/golden/no_policy_lifecycle.json` is regenerated (only the
  `worker_state` flushes, `event_seq`, `ending_offset` and `worker.stream_diagnosis`/
  `owned_processes_seen` moved; statuses, outcomes, exit codes and inspect/explain documents
  are unchanged).
- **Verification.** See the checkpoint commit for the exact commands and results.

### CP5 -- restart recovery (complete)

- **Two-phase `resume` (E).** Phase 1 (`job._supervise_records`) takes no lifecycle lock: for
  each valid, non-terminal (`LAUNCHED`/`COMPLETED`) record of the target that carries
  `worker_state`, it takes the job's supervisor lock (retried `SUPERVISOR_LOCK_RETRY_SECONDS`;
  held -> exit 45 naming the attached Controller's pid from the probe, nothing touched). A
  `COMPLETED` record is left to phase 2. A `LAUNCHED` one: no recorded worker (`STARTING`) ->
  tag scan, a live tagged process is exit 45 naming its pid, command line and `kill`, else phase
  2 fails it closed; an `unverifiable` worker -> phase 2 holds it; worker alive, or gone with
  `ending_offset` -> re-attach and flush `ENDED` + `COMPLETED` (the `completed` event carries
  `reattached: true`); worker gone without `ending_offset` -> no re-attach, drain with the same
  bound, end the anchor, phase 2's `_reconcile_launched`. A re-attached drain that outlives the
  bound writes `drain_detached_at` + `worker_drain_detached` (the shared
  `_record_drain_detached`, also `execute_step`'s) and exits 45. Phase 2 is today's path under
  the lifecycle lock with `_owned_work_hold` (worker verdict, then the recorded group, tag and
  recorded `owned_processes`; recognised daemons excluded) on `LAUNCHED` and `COMPLETED` (D7)
  records; a live owned process is `resume_marked.outcome: owned_work_active` (exit 45).
  Records without `worker_state` take today's paths (I9).
- **Re-attach (`worker.reattach`).** `_ReattachedSupervision` is CP3's `_Supervision` over
  `_FollowedProcess` (the recorded worker and anchor followed by `(pid, start_ticks)`; exit
  status unknown), started from `worker.replay_stream` (the stream replayed from byte 0, then
  the persisted `settled_wakeups`) and the persisted facts; the stall and settle timers start at
  re-attach. It never ends owned work on a failed flush, re-sends the anchor's `SIGUSR1` when
  `ending_offset` is already recorded, and ends the anchor identity-checked. Its ownership has
  no adoption (`scan_recorded_owned_processes`).
- **`abandon`.** Phase 1 (`_abandon_supervision`), for a record with `worker_state`, under its
  supervisor lock: refuses while the worker or an owned process is active (pids, `kill`), or
  unverifiable without the flag; then ends a leftover anchor (`worker.end_recorded_process`)
  before the lifecycle lock.
- **Presentation.** `lock.probe_file_lock` (holder pids); `job.supervisor_probe` (`attached` /
  `unattached` / `unknown`, discounting the record's own anchor); the exit-45 lock text names
  "Job <id>'s stdin anchor (pid A) holds the lock" and `resume` (or its orphan lifetime);
  `pending_reconciliation_jobs` reports the hold; the Ctrl-C announcement names the anchor and
  `resume`.
- **Fake.** `lifecycle_fault` `delay_turn {seconds}` holds a fire's bracket open before its turn.
- **Tests.** `tests/test_resume.py` gains the CP5 list (26 tests): R15 (`WAITING`, with `step`/
  `run` 45 and the anchor text, the probe, the pending report, a second `resume` 45 naming the
  first, and an unrelated `COMPLETED` record untouched mid-supervision and reconciled in phase
  2), `DRAINING`, anchor also lost (`INTERRUPTED`; `FAILED`/`UnreconcilableJobError`), D7,
  abandon with an orphaned anchor, every incomplete-lifecycle-pair case, the settle-window cases
  (restart, wrong match, the load-bearing `settled_wakeups`, validation), loss after `ENDING`
  and after `ENDED`, drain detach (tagged, and the untagged adopted escapee outliving the
  anchor), Ctrl-C, both supervisor-lock-scope windows, the `STARTING` record, the probe and
  the racing acquisition, a legacy record, and replay determinism (R14's two live streams and
  P5 at every line boundary, and mid-line). Rewritten because `resume` now re-attaches instead
  of refusing while an orphan runs: `tests/test_resume.py`'s interruption/orphan tests and
  `tests/test_lifecycle_orchestration.py`'s orphaned apply worker assert `step`'s refusal
  instead; `Lifecycle.rewrite_as(LAUNCHED)` now models a Controller lost before it ended the
  session (`worker_state` `RUNNING`, no `ENDING` facts), since a record lost after `ENDED` is
  re-attached and classified (round 1's O2).
- **Interpretations.** R15's "`status` shows `waiting` and `unsupervised`" and the momentary
  hold's "`status` still shows `unsupervised`" need CP7's presenter; CP5 asserts the facts it
  presents (`worker_state` and `supervisor_probe` `unattached`). In the anchor-also-lost case the
  record is persisted terminal (`INTERRUPTED`, or `FAILED` with `UnreconcilableJobError`), so no
  `resume --abandon` is needed and none is advised. A `resume` whose records end `FAILED` still exits 0, as today.
- **Verification.** See the checkpoint commit for the exact commands and results.

## Next action

`/milestone-implement workflow-controller-worker-lifecycle-ownership` for CP6.
