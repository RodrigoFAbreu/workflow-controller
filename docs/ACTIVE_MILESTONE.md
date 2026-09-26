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
| CP5 -- restart recovery | complete | `4a03168` |
| CP6 -- same-phase durable progress | complete | `e2cb819` |
| CP7 -- operator diagnostics | complete | `f85fa08` |
| CP8 -- documentation and full verification | complete | this checkpoint's commit |

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

### CP6 -- same-phase durable progress (complete)

- **One rule, with a reason (F).** `job._checkpoint_completion_failure` states the unchanged
  `IMPLEMENTING -> IMPLEMENTING` rule once and returns the first unmet condition from the closed
  `CHECKPOINT_PROGRESS_DETAILS` (`pre_state_incomplete`, `head_unchanged`, `state_unreadable`,
  `no_newly_completed_checkpoint`, `last_completed_not_committed`,
  `completion_not_committed_at_head`); `_predicate_checkpoint_completed_durably` is "it returns
  `None`". `last_completed_not_committed` is reported when newly completed checkpoints exist but
  none is committed at `HEAD` and `last_completed_checkpoint_id` advanced to a checkpoint not
  `COMPLETE` in the committed state; an unreadable `HEAD` is `state_unreadable`.
- **One site.** `ExpectedOutcome` gains an optional `predicate_detail`; rows 12/13 declare
  `_checkpoint_completion_failure`. `_row_clauses_failure` evaluates it once (never alongside
  `predicate`) and now returns `(reason, postcondition_detail, predicate_detail)`;
  `_verify_transition` and `_row2_verified` carry it through, so `reconciliation_evidence`
  (`execute_step`, `COMPLETED` resume) and `UnreconcilableJobError`'s evidence and message
  (`LAUNCHED` resume with moved state) name it. `property_table_violations` rejects a
  `predicate_detail` on a row with no predicate. No phase set changed: the
  `SELF_REVIEWING_IMPLEMENTATION` and `APPLYING_REVIEW_FEEDBACK` rows still have no self-loop.
- **The right moment** was delivered by CP4 (verification only after `ENDED` and the drain,
  pinned by `tests/test_lifecycle_orchestration.py`'s predicate-after-launch check); CP6 adds no
  second path.
- **Tests.** `tests/test_job_validation.py`: CP3 -> CP4 committed `FINISHED` (both versions),
  working-tree-only completion (`last_completed_not_committed`, and
  `completion_not_committed_at_head` when the pointer did not move), `head_unchanged`,
  `657c640e`'s shape (`no_newly_completed_checkpoint`), both non-self-loop phases staying
  `phase_not_in_to_any_of` with and without a commit, a clause-by-clause unit test of the detail
  function, and the single-site property (AST: row predicates are called only in
  `_row_clauses_failure`, which only `_verify_transition` and `_row2_verified` call; behaviourally
  both sites evaluate it inside the helper). Existing predicate failures now assert their detail.
  `tests/test_resume.py`: the CP3 -> CP4 `LAUNCHED` record resumes to `FINISHED`; the
  implementation-stage case table carries `predicate_detail` through `COMPLETED` and `LAUNCHED`
  reconciliation.
- **Verification.** See the checkpoint commit for the exact commands and results.

### CP7 -- operator diagnostics (complete)

- **One presenter (G).** `observe.job_activity(record, runtime_root)` derives the activity of a
  record carrying `worker_state` from the record, `job.supervisor_probe` and a fresh
  `job._scan_record_owned` scan: `active`, `waiting` (tasks, pending wakeups with due times, open
  harness commands with stall time, `fire_matched` wakeups "presumed fired, not yet settled" with
  settle time), `draining` (owned pids; after a detach `detached after 10:00 -- end them, then
  workflow-controller resume <repo>`; live recognised daemons as `not owned`), `unsupervised`
  (any live activity with the supervisor lock `unattached`, plus `no Controller attached --
  workflow-controller resume <repo> re-attaches`), `pending reconciliation`, `terminal`. Stall and
  settle times are shown only with a Controller attached (the recorded value plus the time since
  the record's last flush, capped); otherwise `stall time unknown (no Controller attached)` /
  `settle time unknown (no Controller attached; restarts on resume)`. A supervisor probe of
  `unknown` keeps the base activity and says the probe could not be read. Records without
  `worker_state` return `None` and keep every old string.
- **Surfaces.** `status`'s `active:` lines and a new `inspect` `jobs:` block (text beside
  `lifecycle lock:`, JSON `jobs`) use it; both are omitted when the target has no non-terminal
  job, so the no-policy golden is unchanged. `explain` appends `activity:` to each pending job and
  its `--json` `pending_jobs[]` gains `activity`, `worker_state`, `waiting_on`,
  `owned_processes`, `not_owned`, `supervisor`, `resume_command`, `activity_text` (only for
  records carrying `worker_state`). For a `COMPLETED` pending record and for the newest terminal
  job of the target (`last job:` / JSON `last_job`), `explain` prints the stream diagnosis: the
  reason and secondary reasons, the offending `command_uuid` for `command_lifecycle_irregular`
  and `command_lifecycle_unterminated`, one line per anomaly (kind, `command_uuid`, offset; both
  counts for `wakeup_count_mismatch`) and each wakeup's final state and how it got there. Choice:
  the diagnosis is shown only when it carries an anomaly, a wakeup, or one of
  `command_lifecycle_irregular`/`command_lifecycle_unterminated`/`wakeup_not_delivered`, so an
  ordinary exit-status failure adds no block (and the no-policy golden stays byte-identical).
- **follow.** The heartbeat of a non-terminal record carrying `worker_state` is the presenter's
  line (`WAITING` included). With `worker_state`, the worker's exit no longer ends `follow`: it
  keeps following while anything the job owns may run, warns once `no Controller is attached to
  job <id> -- workflow-controller resume <repo> re-attaches; following` when unsupervised, and
  ends (naming `resume`) only at the terminal record or once the job is `pending
  reconciliation` with no Controller attached -- nothing but `resume` can advance it then
  (interpretation of G's "ends only at a terminal record" + "says so and names resume").
  `select_active` and the interrupted-run hand-over also pick a live tracked job whose worker
  already exited (draining). `normalise` renders `task_started`/`task_updated`/
  `task_notification`/`background_tasks_changed` as `background task` lines,
  `command_lifecycle` as `harness command <uuid8> started|completed`, and `_job_text` renders
  `worker_<state>` and `worker_drain_detached` job events.
- **Tests.** `tests/test_observe.py`: every activity row on record fixtures (a sleeper as the
  worker, a sleeper as owned process and as recognised daemon, the supervisor lock held by the
  test as an attached Controller), unresolved brackets and `fire_matched` wakeups with and
  without a Controller, read-only presenting, the follow heartbeat/notice/end rules, the new
  rendering, and the diagnosis lines. `tests/test_cli.py`: `status`, `inspect` (text and JSON),
  `explain` (text and JSON, pending activity, `COMPLETED` diagnosis, `last_job`, and no block for
  a quiet terminal job). Live: R15 (`tests/test_resume.py`) asserts `unsupervised`+waiting after
  the Controller loss and `waiting` with the re-attached `resume` as supervisor; the CP4
  drain-detached run asserts the draining line with the escapee's pid. The
  observation-equivalence suite gains a `WAITING` lifecycle (plain vs `--follow`), normalising
  the fake's random event UUIDs, millisecond timestamps and epoch-ms task times.
- **Verification.** See the checkpoint commit for the exact commands and results.

### CP8 -- documentation and full verification (complete)

- **README.** "Concurrency and worker lifecycle" rewritten: the streaming session and the
  `worker_state` machine, the worker lifecycle note and the disallowed `CronCreate`/`CronDelete`/
  `RemoteTrigger`, the stdin anchor (its two roles and its 60 s orphan lifetime), owned processes
  (group, tag, adoption, recorded entries), the recognised-daemon list and the 600 s drain bound
  with its detach, scheduled wakeups (the `command_lifecycle` bracket, `fire_matched`, settlement
  by a count-checked stop or the 305 s settle window and its cost, the fail-closed cases, H2's
  double-breach residue, the fallback-wakeup delay of decision 11), the two time-based
  harness-contract breaches, `resume`'s two-phase re-attach, and the corrected
  descendant-inherits-the-lock text (tool processes never receive the descriptor; the anchor
  carries it) in the lock paragraph and the exit-45 holder list. "Job dispositions" (resume
  re-attaches and never reconciles held work; abandon's owned-process refusal and anchor
  disposal; records from earlier versions), the command table's `resume` row, "Observing
  workers" (streaming argv, new job events, the activity presenter) and the safety model's
  one-worker bullet updated; the design-record list names the plan and ADR 0004.
- **`docs/adr/0004-worker-lifecycle-ownership.md` (new).** The ownership model, I1-I11, the
  anchor, the owned-process and daemon policy with the drain bound, disallowed tools and the
  lifecycle note, decision 12's measured evidence and rejected recognisers, decision 13 and its
  rejected alternatives, the time-based ends, restart recovery, H1-H9 (H2 with the bracket's
  failure modes and the double-breach residue; H5 with both windows, including re-attached
  supervision), and consequences. No exit code added.
- **`docs/ROADMAP.md`.** Section 1.4 records the hotfix; the four listed patches stay open.
- **Verification** (2026-09-26):
  - `python3 -m unittest discover -s tests -t .`: 1878 tests, `OK (skipped=8)`, 475 s;
  - `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime`:
    9 tests OK;
  - `python3 tools/ci_workflows.py --check`: exit 0;
  - `python3 -m unittest tests.test_plan_document_consistency` (README invocation lines and
    `validate.yml` job names): 40 tests OK;
  - opt-in live contract probe, `CONTROLLER_LIVE_WORKER=1 python3 -m unittest -v
    tests.test_integration_disposable_repo.LiveHarnessContractProbeTest`, against the installed
    `claude` **2.1.283** (the plan measured 2.1.282): 2 tests OK, 493 s.
    `test_streaming_contract_against_the_installed_claude` re-ran P3, P4, P5/P11, P6/P10, P8
    (both), P9 and P11 (every probe exit 0; P5/P11 done at 215.9 s) and the three bracket facts
    held. `test_p12_stop_inside_fire_counts` re-ran both P12 probes (exit 0, done at 74.7 s and
    76.0 s) and the counts held (`cancelledWakeups` 0 single, 1 nested): the final
    re-measurement agrees with CP1's fixtures, so no plan amendment is triggered. The run printed
    only `ResourceWarning`s for unclosed reader pipes inside `capture.py`'s threads (test
    harness, not product code).

## Self-review (SELF_REVIEWING_IMPLEMENTATION, 2026-09-26)

Full milestone diff (`4280bb6..HEAD`) reviewed. No Blocking findings; one Important finding
fixed, none left open.

- **I1 (Important): the anchor's stdin release was not idempotent.** `controller/anchor.py`'s
  `SIGUSR1` handler closed the stdin descriptor number on every signal. A re-attached supervisor
  (`worker.reattach` with `ending_offset` recorded) repeats the request after the lost
  Controller's first one already closed it, and by then the number can belong to one of the
  orphan check's own transient descriptors (`supervisor_attached`'s `os.open`). Closing it there
  makes that function's own `os.close` raise `EBADF`, which ends the anchor early and releases
  the lifecycle lock while tagged work may still drain. The handler now closes the descriptor
  once and ignores later requests; `AnchorTest`'s orphan-lifetime test sends the request twice
  and asserts the anchor still holds on.
- **Verification** (2026-09-26):
  - `python3 -m unittest discover -s tests -t .`: 1878 tests, `OK (skipped=8)`, 472 s. (A first
    run launched as a shell background job failed the two Ctrl-C tests,
    `test_cli.RunRecordCtrlCTest` and `test_resume.InterruptWhileWaitingTest`: a
    non-interactive shell's `&` sets `SIGINT` to `SIG_IGN`, the child Controllers inherit that,
    and the tests' `SIGINT` is ignored. That is an artifact of how the suite was started. Re-run
    with `SIGINT` at its default, everything passed.)
  - `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime`:
    9 tests OK;
  - `python3 tools/ci_workflows.py --check`: exit 0;
  - `python3 -m unittest tests.test_plan_document_consistency`: 40 tests OK.

## Implementation review round 1 (`LOCAL_MODEL_IMPLEMENTATION_REVIEW`, `REVISE`)

- **Important 1, fixed: the anchor's orphan rule counted a tagged recognised daemon as owned
  work.** `anchor.tagged_process_alive` applied no `RECOGNISED_DAEMONS` exclusion, so a
  tag-inheriting `gpg-agent`/`ssh-agent`/Gradle daemon kept an orphaned anchor (and the lifecycle
  lock) alive for the daemon's lifetime, contradicting I4 and the exit-45 anchor sentence.
  `RECOGNISED_DAEMONS` and `daemon_pattern` now live in the stdlib-only `controller/anchor.py`
  and `controller.worker` re-exports the same objects, so the list cannot drift; the anchor
  skips a tagged process whose `/proc/<pid>/cmdline` matches, split exactly as
  `worker._read_cmdline` splits it. New test
  `AnchorTest.test_a_tagged_recognised_daemon_never_keeps_an_orphaned_anchor_alive` (a tagged
  `exec -a gpg-agent sleep` leaves `orphaned()` true; an ordinary tagged sleeper does not; the
  identity pin). ADR 0004's anchor paragraph names the exclusion.
- **Optional 1, noted in code:** `_Supervision._drain`'s group-emptiness test carries a comment
  stating the H9/P2 assumption that a recognised daemon never stays in the worker's group.
- **Optional 2, not applied:** declining `ENDING` once the anchor has died changes `ENDING`
  selection for every dead-anchor case (re-attach included) to cover a window P1 measured at
  ~12 ms, and the fake harness has no delayed-exit-after-EOF mode to test it; left for a later
  round if wanted.
- **Optional 3, not applied:** row 2 (`exit_status`) is "non-zero" in plan B's classification
  table; a signal's secondary `exit_status` is harmless and matches the plan text.
- **Verification** (2026-09-26): full suite 1879 tests `OK (skipped=8)`;
  `tools/ci_workflows.py --check` exit 0; `tests.test_plan_document_consistency` OK;
  `tests.test_packaged_runtime` (packaging required) OK.

## Functional review checklist

This checklist covers implementation revision 2, reviewed at `f0a86d2`, with technical approval
`4a65024`. The automated state is current: at `f0a86d2` the full suite ran 1879 tests, OK (8
opt-in skips), `tests.test_packaged_runtime` (packaging required), `tools/ci_workflows.py --check`
and `tests.test_plan_document_consistency` passed. Since then only `WORKFLOW_STATE.json` and this
file have changed. Record findings in `.ai-review/feedback/FUNCTIONAL_REVIEW.md`.

Before this checklist was committed, every flow below was dry-run against a pipx install of a
wheel built from a clean clone of `4a65024`. Two results did not match the expected text; see
"Dry-run notes" at the end. Flows F1-F7 cost nothing: the worker is `tests/fake_claude.py` in
streaming mode, scripted to own real background processes, and the Workflow Manager is the
offline test stub. Nothing in this repository is modified. Only F8 calls the real `claude`.

### Setup

1. `pipx` is installed. You need **two zsh terminals**, called A and B below.
2. In terminal A, write the session file:

   ```zsh
   mkdir /tmp/wc-lifecycle-fr && cat > /tmp/wc-lifecycle-fr/session.zsh <<'EOF'
   C=/home/rodrigo/Workspace/workflow-controller; W=/tmp/wc-lifecycle-fr; unset PYTHONPATH
   export C W PIPX_HOME=$W/pipx PIPX_BIN_DIR=$W/bin PIPX_MAN_DIR=$W/man XDG_STATE_HOME=$W/xdg
   export WC=$W/bin/workflow-controller
   use() { . $W/$1/env; J=$(ls $RT/jobs 2>/dev/null | sed -n 's/\.json$//p' | head -1); }
   wcx() { (cd /tmp; $WC --runtime-dir $RT --workflow-manager $SM --claude-binary $C/tests/fake_claude.py "$@") }
   drive() { (cd /tmp; python3 $W/drive.py "$@") }
   rec() { python3 -c "import json, sys; r = json.load(open(sys.argv[1])); print(eval(sys.argv[2]))" $RT/jobs/$J.json "$1"; }
   hist() { python3 -c "import json, sys; print(' '.join(e['event'] for e in map(json.loads, open(sys.argv[1])) if e.get('state_changed') is not False))" $RT/jobs/$J/events.jsonl; }
   EOF
   ```

   Then run `. /tmp/wc-lifecycle-fr/session.zsh` in **both** terminals.
   - `drive stop <name> <scenario>` builds a disposable `"2.2"` target under `$W/<name>`: a work
     item at `IMPLEMENTING` whose scripted `/milestone-implement` worker plays `<scenario>`.
   - `use <name>` loads that target into the shell: `$R` (the target), `$RT` (its runtime root),
     `$REL` (the file that releases the scripted "full verification"), and `$J` (its job id, once
     a job exists; rerun `use <name>` after the first `step`).
   - `wcx <args>` runs the installed Controller against the loaded target. `rec '<expr>'`
     evaluates a Python expression over the job record `r`. `hist` prints the job's event
     history, one entry per state change.
3. In terminal A, save the driver from this file, build a wheel from a clean clone of this
   commit, install it with pipx, and delete the clone:

   ```zsh
   awk '/^<!-- drive.py begin -->$/{f=1;next} /^<!-- drive.py end -->$/{f=0} f' $C/docs/ACTIVE_MILESTONE.md | sed '1d;$d' > $W/drive.py
   git clone -q $C $W/clone && (cd $W && python3 -m pip wheel -q --no-deps --no-build-isolation --wheel-dir $W/dist $W/clone)
   pipx install -q $W/dist/*.whl && rm -rf $W/clone && $WC --version
   ```

   Expected: `$W/drive.py` starts with `"""Functional-review driver`. There is one wheel,
   `$W/dist/workflow_controller-1.1.1-py3-none-any.whl`. `--version` prints
   `workflow-controller 1.1.1` and `runtime: package (local build from <this commit>)`.

<!-- drive.py begin -->
```python
"""Functional-review driver: disposable "2.2" targets at IMPLEMENTING whose
scripted streaming worker owns background work, for the installed
Controller. Run as: python3 $W/drive.py stop <name> <scenario>"""
import os, shlex, sys, tempfile
from pathlib import Path

C, W = Path(os.environ["C"]), Path(os.environ["W"])
sys.path.insert(0, str(C))
from tests import fixtures  # noqa: E402
from tests import test_lifecycle_orchestration as lifecycle  # noqa: E402

SAYS = lifecycle.SAYS_IT_WILL_CONTINUE


class Case(lifecycle._LifecycleTestCase):
    def runTest(self) -> None:
        pass


def turns(scenario: str, lc: lifecycle.Lifecycle, d: Path) -> list:
    """The worker's scripted turns: turn 0 answers the task; each later
    turn is the one a background task's completion starts."""
    release, done, orphan = d / "release", d / "verification-done", d / "orphan-done"
    implement = [{"step": "actions", "actions": lc.implement("CP1")}]
    if scenario in ("wait", "ctrlc", "lose", "abandon"):
        return [[lifecycle.verification(done, release=release), SAYS], implement]
    if scenario == "drain":
        return [[{"step": "bash_bg", "id": "suite", "seconds": 0.5, "orphan": "reparent", "orphan_seconds": 40,
                  "orphan_write_file": str(orphan), "description": "full verification"}, SAYS], implement]
    if scenario == "daemon":
        return [[{"step": "bash_bg", "id": "build", "seconds": 0.5, "orphan": "daemon", "argv0": "gpg-agent",
                  "orphan_seconds": 600, "description": "signed build"}, SAYS], implement]
    if scenario == "wakeup":
        return [[{"step": "wakeup", "delay": 30, "prompt": "check the verification", "reason": "verification",
                  "fire_turn": [*implement, {"step": "wakeup_stop"}]},
                 {"step": "text", "text": "I will continue when the wakeup fires."}]]
    raise SystemExit(f"unknown scenario {scenario}")


def stop(name: str, scenario: str) -> None:
    d = W / name
    d.mkdir()
    tempfile.tempdir = str(d)
    Case.setUpClass()
    Case._class_tmp._finalizer.detach()
    c = Case()
    c.setUp()
    c._tmp._finalizer.detach()
    lc = c.seed("case", lifecycle.IMPLEMENTING)
    lc.add(lifecycle.MILESTONE_IMPLEMENT, {"turns": turns(scenario, lc, d)})
    fixtures.write_worker_script(lc.script_path, lc.script)
    lc.runtime.mkdir(parents=True, exist_ok=True)
    env = {"R": str(lc.root), "RT": str(lc.runtime), "SM": str(c.stub_manager),
           "FAKE_CLAUDE_SCRIPT": str(lc.script_path), "FAKE_CLAUDE_INVOCATIONS_FILE": str(lc.processes_file),
           "REL": str(d / "release")}
    (d / "env").write_text("".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items()))
    print(f"-- driver: {name} is a target at IMPLEMENTING ({scenario}); now run: use {name}")


if __name__ == "__main__":
    command, rest = sys.argv[1], sys.argv[2:]
    {"stop": lambda: stop(*rest)}[command]()
```
<!-- drive.py end -->

### Test data

No seeding is needed beyond the driver. Each scenario gets its own target: a git repository with
a registry of `CP1`/`CP2`, a `WORKFLOW_STATE.json` at `IMPLEMENTING`, and a worker script. The
worker's first turn starts background work and ends its turn saying it will continue. A later
turn (a task's completion, or a wakeup's fire) implements `CP1`, which means a commit `Implement
CP1` and `last_completed_checkpoint_id=CP1`. Build every scenario once:

```zsh
for s in wait ctrlc lose drain daemon wakeup abandon; do drive stop $s $s; done
```

Expected: seven `-- driver: <s> is a target at IMPLEMENTING (<s>)` lines.

### Flows

**F1 -- a waiting worker keeps its session and the worktree (the core fix).**
1. A: `use wait; wcx step $R`. It does not return.
2. B: `use wait`, then:
   - `wcx status`. Expected: under `active:`, the run (`controller pid <n> active`) and `job <J>
     (LAUNCHED, ...): worker pid <p> waiting on 1 background task (verify "full verification")
     and 0 wakeups; owns 2 processes (...)`, each with a `follow:` line.
   - `wcx inspect $R`. Expected: `lifecycle lock: held`, then a `jobs:` block with the same
     activity line. `last_completed_checkpoint_id=None`.
   - `wcx explain $R`. Expected: `pending job: <J> (LAUNCHED)` followed by an `activity:` line
     with the same text.
   - `timeout 15 $WC --runtime-dir $RT follow $R`. Expected: the worker's first turn (`background
     task verify started: full verification`, the "running in the background" text, `worker
     result: success`), and `worker WAITING` job lines. It does **not** end at the worker's
     `result`.
   - `wcx step $R; echo $?` and `wcx run $R; echo $?`. Expected: both exit `45`. The message names
     `Job <J>'s stdin anchor (pid <a>) holds the lock`, says `a Controller (pid <n>) is attached to
     the job, supervising it`, and says a tool process never receives the descriptor.
   - `touch $REL`.
3. A: `step` returns within a few seconds, exit `0`.
4. B: `use wait; hist; git -C $R log --oneline -1; wcx inspect $R | grep -E 'lock|last_completed'`.
   Expected: `planned launched worker_spawned worker_running worker_waiting worker_running
   worker_ending worker_ended completed finished`, `Implement CP1`, `lifecycle lock: free` and
   `last_completed_checkpoint_id=CP1`. The worker's second turn ran in the **same** session:
   `rec "r['worker']['stream_diagnosis']['turns']"` prints `2`.

**F2 -- Ctrl-C ends only the Controller; `resume` re-attaches.**
1. A: `use ctrlc; wcx step $R`. In B: `use ctrlc`, then `wcx status | grep waiting` until it
   prints the job's waiting line.
2. A: press Ctrl-C. Expected on stderr: `interrupted -- the worker (pid <p>, process group <p>)
   keeps running in its own session ...`, `its stdin anchor (pid <a>) keeps the session open and
   holds the lifecycle lock; `workflow-controller resume <R>` re-attaches ...`, and a `follow it:`
   line. A Python `KeyboardInterrupt` traceback follows, because the interrupt propagates
   unchanged (see known limitations).
3. B: `use ctrlc; wcx status | grep ' job '`. Expected: still `waiting on 1 background task`, now
   ending `; no Controller attached -- workflow-controller resume <R> re-attaches`. Then `wcx
   step $R; echo $?`. Expected: `45`, naming the stdin anchor, `no Controller is attached`, and
   `resume`.
4. A: `wcx resume $R`. It does not return.
5. B: `wcx status | grep ' job '`. Expected: `waiting`, with no `no Controller attached` suffix.
   `wcx resume $R; echo $?`. Expected: `45`, `supervisor lock ... is held by another Controller
   (pid <resume's pid>) ... nothing was reconciled`. Then `touch $REL`.
6. A: `resume` prints `<J>: FINISHED`, exit `0`. B: `git -C $R log --oneline -1` shows
   `Implement CP1`.

**F3 -- the Controller is killed outright (`SIGKILL`); `resume` re-attaches.**
1. A: `use lose; wcx step $R`. B: `use lose`, then wait until `wcx status | grep waiting` prints.
2. B: `pkill -KILL -f -- "-m controller .*step $R\$"`. A's `step` dies (`killed`).
3. B: `wcx status`. Expected: the run shows `controller pid <n> inactive`, and the job shows
   `waiting ...; no Controller attached -- workflow-controller resume <R> re-attaches`. `wcx
   inspect $R` shows `lifecycle lock: held` and the same `jobs:` line.
4. A: `wcx resume $R`. B: `touch $REL`. Expected in A: `<J>: FINISHED`, exit `0`.
5. B: `use lose; hist`. Expected: `... worker_waiting worker_running worker_ending worker_ended
   completed reconciled`. `git -C $R log --oneline -1` shows `Implement CP1`.

**F4 -- an orphaned process the job owns is drained before the job completes.** This scenario's
background task leaves a double-forked child in the worker's group that lives 40 s.
1. A: `use drain; time wcx step $R`.
2. B: within the 40 s, `use drain; wcx status | grep ' job '`. Expected: `worker pid <p> ending
   (DRAINING); 2 owned processes still running (pids ...)`. `wcx explain $R | grep activity`
   shows the same.
3. A: expected on stderr `worker pid <p> exited; waiting for 2 process(es) still in its process
   group: ...`, then exit `0` after about 40 s.
4. B: `use drain; hist`. Expected: `... worker_ending worker_exited worker_draining worker_ended
   completed finished`. The orphan finished before the record completed: `cat
   $W/drain/orphan-done` (the orphan's end time) is earlier than `date -r $RT/jobs/$J.json +%s.%N`.

**F5 -- a recognised tool daemon is never owned.** The worker's build leaves a long-lived
process named `gpg-agent` in its own session, carrying the job's ownership tag.
1. A: `use daemon; time wcx step $R`. Expected: exit `0` within about
   2 s. The job does not wait for the daemon.
2. A: `use daemon; rec "r['worker_state']['excluded_processes']"; rec "r['status']"`. Expected:
   one entry with `pattern: gpg-agent` and its pid, and `FINISHED`. `ps -o pid,args -p <that
   pid>` shows `gpg-agent 600` still running. `wcx inspect $R | grep lock` shows `lifecycle
   lock: free`.
3. Clean up: `kill <that pid>`.

**F6 -- a scheduled wakeup keeps the session open and is settled by its stop.** The worker
schedules a 30 s wakeup, ends its turn, and implements `CP1` in the fire turn, then stops the
wakeup.
1. A: `use wakeup; date +%T; wcx step $R`.
2. B: `use wakeup; wcx status | grep ' job '`. Expected: `waiting on 0 background tasks and 1
   wakeup (due HH:MM:SS)`, where the due time is 30 s after the `date` printed in step 1, and
   stays the same if you run the command again.
3. A: exit `0` about 30 s after the start.
4. B: `wcx explain $R`. Expected: `last job: <J> (FINISHED)`, `worker outcome SUCCESS:
   quiescent_terminal_turn`, and `wakeup toolu_fake_0001: settled by stop (cancelledWakeups 0,
   expected 0), inside its own fire's harness command <uuid>`. `wcx --json explain $R` carries
   the same data under `last_job.stream_diagnosis.wakeups`. `git -C $R log --oneline -1` shows
   `Implement CP1`.

**F7 -- `resume --abandon` refuses while owned work runs, then ends the anchor.**
1. A: `use abandon; wcx step $R`. B: `use abandon`, then wait until `wcx status | grep waiting`
   prints.
2. B: lose the Controller and the worker, but not the verification:
   `pkill -KILL -f -- "-m controller .*step $R\$"; use abandon; kill -KILL $(rec "r['worker_process']['pid']")`.
3. B: `wcx resume --abandon $J $R; echo $?`. Expected: `45`, `refused, and no flag overrides it`,
   naming the dead leader and the running processes still in its recorded process group, with
   the `ps -o pid,pgid,lstart,args -g <pgid>` and `kill -TERM -- -<pgid>` commands.
   `rec "r['status']"` is still `LAUNCHED`.
4. B: `kill -TERM -- -$(rec "r['worker_process']['pgid']")`, then `wcx resume --abandon $J $R;
   echo $?`. Expected: `<J>: FAILED (OperatorAbandoned; was LAUNCHED; ...)`, exit `0`. `ps -eo
   args | grep -c "[W]orker's stdin anchor"` prints `0`: the leftover anchor was ended. `wcx
   inspect $R | grep lock` shows `lifecycle lock: free`, and `git -C $R log --oneline -1` is still
   `Seed the work item`.

**F8 (optional, real `claude`, real spend, about 10 minutes) -- the harness contract still holds
on your installed `claude`.** From `$C`: `claude --version`, then `CONTROLLER_LIVE_WORKER=1
python3 -m unittest -v tests.test_integration_disposable_repo.LiveHarnessContractProbeTest`.
Expected: 2 tests OK. CP8 ran this against `claude` 2.1.283. A failure here means the measured
contract (a session kept open while stdin is open, the `command_lifecycle` wakeup bracket, the
P12 stop counts) changed. Report the version and the failing probe.

**Teardown:** `pipx uninstall workflow-controller; rm -rf /tmp/wc-lifecycle-fr`. Check `ps -eo
pid,args | grep -E '[f]ake_claude|[g]pg-agent 600'` prints nothing.

### Known limitations and out of scope

- Ctrl-C prints a Python `KeyboardInterrupt` traceback after the three guidance lines. The
  interrupt propagates unchanged by design (plan C); the Controller never forwards it to the
  worker.
- The 305 s settle window, a matched wakeup that is never stopped, and the two 300 s
  harness-contract breaches (`wakeup_not_delivered`, `command_lifecycle_unterminated`) are too
  slow to walk by hand. They are covered by the suite and the README's "Time is not
  termination".
- The 600 s drain bound and its detach (`detached after 10:00`) are covered by the suite, not by
  a flow here.
- These are unsolved by design (README "What is not solved here", ADR 0004): the model relies on
  the measured harness behaviour; wakeup fires are matched by an undocumented event; an
  `env -i` descendant orphaned while nothing supervises it is not owned; daemon recognition is by
  name.
- Implementation review round 1's Optional 2 (declining `ENDING` once the anchor has died) and
  Optional 3 were not applied. The four patches listed in `docs/ROADMAP.md` section 1.4 stay
  open.
- `--timeout` is now opt-in and spans the whole owned lifetime. There is no default worker
  timeout.

### Dry-run notes

The dry run matched the expected results above except in two places. They are recorded here so
you can confirm them and file them in `FUNCTIONAL_REVIEW.md`:

- **F6: the wakeup's due time shows the current time.** The dry run printed `(due 02:39:45)` at
  02:39:45 for a wakeup recorded as `due_at: 2026-09-26T01:40:10.201Z` (02:40:10 local). The
  recorded `due_at` has milliseconds. `observe._parse_at` accepts only whole-second
  `%Y-%m-%dT%H:%M:%SZ`, so `_clock` falls back to "now". The recorded `due_at` and the
  `explain --json` data are correct. Any other presenter time built from the same millisecond
  field would show the same fault.
- **F1/F2/F3/F4/F7: `owns 2 processs` / `2 owned processs still running`.** The noun is
  pluralised by appending `s` to `process`.

One more observation, not necessarily a defect: in F7, after the worker leader is killed but its
group still runs, `status` still describes the job as `worker pid <p> waiting on 1 background
task`. It is derived from the last recorded `worker_state`, and no Controller is attached to
update that state.

## Next action

At the `AWAITING_FUNCTIONAL_REVIEW` hard gate: the user runs the checklist above and records
findings in `.ai-review/feedback/FUNCTIONAL_REVIEW.md`. With no findings, `/accept-milestone`.
With findings, `/apply-functional-review`.
