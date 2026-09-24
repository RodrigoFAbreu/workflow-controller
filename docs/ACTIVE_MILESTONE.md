# Active Milestone

## Status

**Implementing.** `workflow-controller-release-runtime-observability`, governing workflow version
`2.2`, base commit `16c3fb4670bfeb9c7ce82aaff13f5a643a3f2400` (the acceptance commit of
`workflow-controller-automatic-lifecycle-orchestration`). Plan revision 6 was approved by both
plan-review stages (approval commit `3397e39`). `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground truth for the phase and for each checkpoint's status.

## Goal

Turn the Workflow Controller into an isolated, versioned, releasable runtime, and add live
observation of the workers it launches, without changing lifecycle semantics:

- a single semantic version source, `workflow-controller --version`, and a build identity
  (`controller/BUILD_INFO.json`) embedded in every wheel;
- a packaged (non-editable wheel/pipx) runtime that launches workers without Git or a source
  checkout. This fixes the reproduced `git archive failed` defect and the missing
  `GENERATION.json` in the wheel;
- durable Controller runtime/release identity in job records, `identity.json` and `status`;
- GitHub Actions: parallel `fail-fast: false` matrix validation with same-ref cancellation, and a
  tag-triggered release gated on validation that verifies the tag, version and wheel, refuses
  duplicates, and publishes an immutable GitHub Release;
- `stream-json` worker output teed to durable per-job logs, Controller lifecycle event logs,
  `step/run --follow` and a zero-write `follow` attach command. Observation is presentation-only.

## Plan

- Plan: `docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md`
- Registry: `docs/ai-workflow/registry/workflow-controller-release-runtime-observability-registry.json`
  (checkpoints `CP1`-`CP11`)
- Requirement map: `docs/ai-workflow/requirements/workflow-controller-release-runtime-observability-mapping.json`
  (`R1`-`R13`)

## Carried-over follow-ups (not in this milestone's scope)

These are from `workflow-controller-automatic-lifecycle-orchestration` and remain deferred:
- the manual-external gate can name a stale ledger `review_content_id` after a failed local-review
  job (O1);
- gates' `explain --work-item <id>` resume hint does not parse as written (O2);
- no explicit test pins `OperatorAbandoned`/`UnreconcilableJobError` records in the apply
  relaunch bound (O3).

The previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-automatic-lifecycle-orchestration.md`.

## Checkpoint progress

- **CP1 -- version source, build identity, `--version`: complete.**
  - `controller/version.py` (`__version__ = "1.1.0"`) is the single version source. `pyproject.toml`
    reads it as the dynamic version and now ships `controller/GENERATION.json` as package data.
  - `controller/buildinfo.py` (dependency-free, first in the import order) holds
    `compute_package_digest`, `validate_build_info`, `SEMVER_RE` and the tag helpers.
  - New `setup.py`: a `build_py` hook that writes `build_lib/controller/BUILD_INFO.json`. It
    returns early for editable installs, forces re-copies, and refuses a stale `build/`. A release
    build needs `WORKFLOW_CONTROLLER_RELEASE_TAG`.
  - `workflow-controller --version` prints `workflow-controller 1.1.0`. The runtime line lands in
    CP2.
  - Verified: the baseline suite passed before the first edit (982 tests, 6 skipped). Then
    `tests.test_buildinfo tests.test_package_structure tests.test_cli` passed (109 tests), and the
    full suite passed with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1` (1016 tests, 6 skipped).

- **CP2 -- packaged runtime identity: complete.**
  - `identity.resolve_runtime(code_root)` classifies the running code as `package` (a valid
    `controller/BUILD_INFO.json` and no `.git`), `source` (a `.git` whose top level is `code_root`
    and which tracks `controller/__init__.py`) or `unidentified`. It never reads
    `direct_url.json`. A `BUILD_INFO.json` inside a checkout is `unidentified` ("delete it to run
    from source"), decided without Git. A package or unidentified runtime whose root has no
    `.git` makes no Git call at all.
  - `ControllerIdentity` gains `runtime_kind`, `version`, `build` and `runtime_reason`.
    `source_kind` gains `package`. `pin()`'s unpinned identity takes a package's commit from its
    build info, and only when the build was clean.
  - `materialise` dispatches on the kind. A package is copied without Git (regular files only; a
    symlink is refused), its `package_digest` is re-checked on the copy, and the generation is
    read from the copy. A dirty or unknown-provenance build needs `--allow-dirty-source`. An
    unidentified runtime refuses (exit 20). `SOURCE_PIN.json` gains `runtime_kind`, `version`,
    `build` and `controller_runtime`. A pin without `runtime_kind` reads as `source`.
  - Ladder row 3 applies only to `source` runtimes, which fixes the venv-inside-checkout
    misfire.
  - `handoff.detect` for a package compares against the installed package's
    `GENERATION.json`, with no Git. `handoff.json`'s `running`/`approved` blocks gain `version`.
  - `identity.runtime_record` is the `controller_runtime` block, written into `identity.json` and
    every job record. `inspect`/`explain --json` carry it as `controller`. `status` opens with a
    `controller:` line, and `--version` prints `describe_runtime` as line 2. The source dirty
    probe now runs with `git --no-optional-locks`, so `--version`/`status` never refresh the
    index.
  - Verified: the narrow set `tests.test_identity tests.test_runtime tests.test_handoff
    tests.test_cli tests.test_job tests.test_job_validation tests.test_resume
    tests.test_package_structure` passed (441 tests). The full suite with
    `CONTROLLER_REQUIRE_PACKAGING_TESTS=1` passed (1047 tests, 6 skipped).
  - Known flake, not caused by CP2: `test_worker.InterruptedTest.test_hanging_worker_with_short_timeout_classifies_interrupted_and_reaps_group`
    failed in 3 of 13 full-suite runs, back to back, and passed in every other run and in
    isolation. It checks the killed grandchild's liveness right after the timeout, without
    waiting for the orphan to be reaped. CP2 does not touch `worker.py` or that test.

- **CP3 -- packaged-runtime regression suite: complete.**
  - New `tests/test_packaged_runtime.py`. Each class builds one wheel from a committed
    generation-1 `build_checkout` clone, installs it non-editably into a fresh venv
    (`fixtures.wheel_install`, `pip install --no-deps --no-index`), and runs the installed
    console script as a subprocess. It uses the stub Workflow Manager and `tests/fake_claude.py`,
    which performs `/milestone-plan`'s transition on a `"2.1"` `PLANNING` target. The module
    skips on a missing build prerequisite, or fails with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`.
  - Cases:
    1. the reproduced defect: `step` exits `0` with a `FINISHED` job and no `git archive` in its
       output;
    2. and 3. the checkout deleted, then renamed: `--version`, `inspect`, `status` and `step` all
       work, with the build's identity;
    4. the checkout moved to `9.9.9` and generation 2 after install: the install keeps its
       identity, with no handoff and an XDG runtime root;
    5. a venv inside the checkout: `status`, `inspect` and `step` record the build's commit, not
       the clone's new `HEAD`, and no `.controller` appears under the venv or the clone;
    6. `--version`, `METADATA` and `BUILD_INFO` agree;
    7. an edited installed byte: exit `20`, naming the package digest, with no worker and no job;
    8. a generation-2 wheel reinstalled under a paused `run`: exit `50`, and `handoff.json`
       names both versions;
    9. an editable install is still `source`/`commit` with row 3, and a dirty edit still needs
       `--allow-dirty-source`.
  - Read-only commands run unpinned, so their `controller_runtime` has `source_kind: unpinned`
    and `generation: null`, the same as a source runtime's. The suite asserts that as the
    behaviour, not a defect.
  - `tests/fixtures.py` gains `wheel_install`. `build_checkout` already copied `setup.py` (CP1).
  - Verified: `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime
    -v` passed (9 tests). `tests.test_buildinfo tests.test_package_structure
    tests.test_plan_document_consistency tests.test_handoff tests.test_identity` passed (152
    tests). No product code changed.

- **CP4 -- streaming worker output: complete.**
  - `worker.launch` runs `claude -p <task> --output-format stream-json --verbose
    --permission-mode <mode> [--model] [--effort] [--disallowedTools]`, with the disallow list
    still last. It takes two new required arguments, `stdout_path`/`stderr_path`: files the
    caller has created. It opens each `O_WRONLY | O_APPEND`, hands them to the worker, and closes
    its own copies, so the worker writes its stream into the durable log itself, with no pipe.
  - Control returns when the direct child has exited and its process group is empty. Phase 1 is
    `proc.wait()`. Phase 2 is a group drain that rescans the group every 0.2 s through
    `process_test`. A zombie counts as gone, and under the `killpg` form only
    `killpg_no_such_group` ends the drain. A `--timeout` expiring in phase 2 ends the group through
    the new `_kill_drained_group` (a direct `killpg`, never `os.getpgid`), then waits at most 2 s
    for the scan to report it empty. `_classify` gains `timed_out`, checked first, so
    `INTERRUPTED` with exit code `0` is a clean exit whose group outlived the budget. The new
    `on_group_drain(pid, remaining_pids)` callback has `on_spawn`'s contract.
  - `_parse_worker_stream` replaces `_parse_worker_stdout`. Every line must be one JSON object,
    and exactly one `result` event must come last. Anything else is `AMBIGUOUS`. `WorkerResult`,
    the field lists and `_worker_dict` are unchanged, and `raw_json` is the result event.
  - `job`: after the `PLANNED` flush, `runtime.create_log_file` (new: contained, `O_EXCL`, mode
    `0o600`) creates `jobs/<id>/worker.stdout` and `worker.stderr`. A failure is wrapped in
    `WorkerLaunchError` and recorded `FAILED`/`WorkerNotStarted` (exit `20`). The `LAUNCHED`
    flush carries `worker_streams` (`format`, `stdout_path`, `stderr_path`, `events_path`). The
    drain callback persists `worker_group_drain` and writes one
    `worker pid P exited; waiting for N process(es) ...` line to stderr, ignoring an `OSError`.
    `_write_worker_streams` is gone.
  - `tests/fake_claude.py` emits a five-event stream by default. It refuses `stream-json`
    without `--verbose` with the real CLI's message, and gains `FAKE_CLAUDE_EVENT_DELAY`,
    `FAKE_CLAUDE_PAUSE_AFTER_EVENTS_FILE` (`<n>:<path>`), `FAKE_CLAUDE_DESCENDANT`
    (`group:`/`group-closed:`/`setsid:<seconds>`) and `FAKE_CLAUDE_DESCENDANT_FILE`. It also
    exposes `result_event`/`default_events`/`stream_text` for the tests.
  - New `tests/golden/claude_stream_json_2.1.281.jsonl`: a real `claude -p "Reply with the word
    ok." --output-format stream-json --verbose --model haiku` transcript (claude 2.1.281, 7
    lines, USD 0.018), sanitised as its sibling `.md` records.
  - **Test-policy change for the reviewer:** `tests/test_write_containment.py`'s package-wide
    scan flagged `launch`'s `os.open(path, os.O_WRONLY | os.O_APPEND)`. `launch` has no runtime
    root to check against, and containment is enforced where the files are created. So the scan
    now accepts exactly that flag pair at exactly `("worker.py", "launch")`. Adding `O_CREAT` or
    `O_TRUNC` there, or using the form anywhere else, is still flagged, and synthetic tests pin
    both.
  - Verified: the narrow set `tests.test_worker tests.test_job tests.test_resume
    tests.test_lifecycle_orchestration` passed (264 tests). `tests.test_write_containment` passed
    (10). The full suite passed (1085 tests, 6 skipped), and so did
    `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 tests.test_packaged_runtime` (9).

- **CP5 -- lifecycle event logs and run records: complete.**
  - `runtime.append_jsonl` (contained, `O_APPEND | O_CREAT`, mode `0o600`, one `write()` per line,
    no `fsync`; the object is serialised before the file is opened). `append_jsonl_best_effort` and
    `write_json_best_effort` catch `Exception` (never `BaseException`) and print
    `workflow-controller: warning: could not write <path>: ...` once per process. The warning itself
    never raises.
  - `job._persist(..., event=, details=)` increments the record's additive `event_seq`, writes the
    record (still the authority, still raising), then appends
    `{"v": 1, "seq", "at", "job_id", "event", ...}` to `jobs/<id>/events.jsonl`, best-effort.
    Every call site keeps the returned record. The events are `planned`, `launched`,
    `worker_spawned` (pid, pgid), `worker_exited` (pid, remaining pids), `completed` (outcome, exit
    code), `finished`/`failed`/`incomplete` (observed phase, `transition_verified`),
    `gate_blocked`/`declined`/`handoff_pending` (reason), `worker_not_started` (error),
    `reconciled` (status, code) and `abandoned`. A missing or malformed `event_seq` starts at `1`
    and never raises. `--abandon`'s replace path carries only a valid prior `event_seq`.
  - `job.RunRecord`/`open_run`: `runs/<run_id>.json` (`schema_version`, `run_id`, `command`,
    `target_repo`, `max_steps`, `controller_process`, `controller_runtime`, `state`, `exit_code`,
    `job_ids`, `current_job_id`, `started_at`/`updated_at`/`ended_at`) and
    `runs/<run_id>/events.jsonl` (`run_started`, `step_started`, `job_started`, `job_ended`,
    `handoff_detected`, `no_action`, `run_ended`/`run_interrupted`). Every write is best-effort.
    `execute_step(run_id=)` records `run_id` on the job and mirrors the job's start and end into the
    open run's log, through a per-process registry.
  - `cli`: `cmd_step`/`cmd_run` create the run after `_inspect_target` and register it in
    `_open_run`. `main()` closes it once, in a `finally`: `ended` with the code it returns (including
    `20` and `45`), or `interrupted` with `exit_code: null` on `KeyboardInterrupt`. Any other
    exception leaves the run `running`, like a killed Controller. A stale registration (from a
    direct `cmd_*` call) is discarded, never closed.
  - The `--follow` half of the `command` test uses an args namespace with `follow=True`, because the
    flag lands in CP6. The record takes `command` from the subcommand, never argv.
  - Verified: the narrow set `tests.test_job tests.test_cli tests.test_resume tests.test_observe
    tests.test_write_containment` passed (277 tests). The full suite passed (1121 tests, 6
    skipped). A read spy on `open`/`os.open`/`Path.read_*` pins that `resume`,
    `pending_reconciliation_jobs`, `_classify_jobs` and `abandon` read nothing under `runs/` or
    `jobs/<id>/`, and a negative control confirmed that the spy catches such a read.

- **CP6 -- observation surface: complete.**
  - New `controller/observe.py`, placed after `job` and before `cli` in `__init__` and the
    dependency-order test. It imports only `job` (statuses, run states) and `worker` (liveness
    readers), and it only reads.
    - `Tail` reads a file by offset, holds back a partial trailing line (`flush()` releases it on
      the final drain, so a legacy newline-less `worker.stdout` replays), and tolerates a file
      that appears late. It never opens anything for writing.
    - `normalise(source, line, tools)` returns a list of presentation events. It never raises: a
      non-JSON line is `raw` and an unknown type is `unknown`. A message with several content
      blocks yields several events, and thinking/redacted-thinking blocks yield none.
      `tool_result` names its tool through the `tool_use` id map. `render_text` prefixes local
      time and a source column and indents continuation lines. `render_json` prints one object
      per line.
    - `follow_run`/`follow_job` multiplex the run log, each job's `events.jsonl`,
      `worker.stdout` and `worker.stderr`. A `job_ended`/`run_ended` event is emitted after that
      job's (or every job's) pending lines, so replays read in order. Without `--from-start`
      the last 20 events are replayed. The record is written before its event is appended, so
      the final drain waits up to 2 s for `run_ended`/`run_interrupted`, or for the job's
      `event_seq`.
    - Endings: an `ended` run; a `running` run whose Controller is `inactive` ("controller
      process is gone; run record was not closed"); an `interrupted` run, which hands over to
      the job while its worker is `active`. A job ends when its record is terminal, or when its
      worker is not `active` ("worker exited; job <id> awaits resume"). **Design choice for the
      reviewer:** a job whose own run is still `running` with a live Controller is never ended
      by its worker's exit, because that Controller is still verifying it. A non-terminal job
      with no recorded worker ends with "no worker is recorded for job <id>; ...".
      `unverifiable` Controllers and workers warn once and keep following.
    - `controller_liveness` is pid-level: same boot and pid namespace, the pid is present with
      the recorded `start_ticks`, and it is not a zombie. It uses `worker`'s `/proc` readers and
      never the process group.
    - Heartbeats (`HEARTBEAT_SECONDS`, 30 s by default and overridable) print `worker running:
      pid P, elapsed M:SS, last event Ns ago`, or the drain wording when the record carries
      `worker_group_drain`.
    - `FdSink` writes with `os.write` to `os.dup(2)`, in chunks of at most `PIPE_BUF`, after a
      `poll(POLLOUT)` wait of at most 1 s. A wait that times out disables it. On a FIFO it also
      disables itself rather than leave less than 16 KiB free (`F_GETPIPE_SZ` − `FIONREAD`).
      `fail()` makes one attempt at a note and then disables it.
  - `cli`:
    - `follow [--job ID | --run ID] [--from-start] [<repo>]` is dispatched at the top of
      `_dispatch`, before `pin()`, runtime-root creation, materialisation and the
      `identity.json` write. The runtime root comes from `identity.resolve_runtime(code_root)`
      (or a snapshot's `SOURCE_PIN.json`) through `resolve_runtime_root`. `<repo>` resolves to
      the Git top level, the same value as `target_repo`. An unknown id or another target's
      record is a plain `ControllerError` (exit 20). Ctrl-C ends the follow with exit 0.
    - `--follow` on `step`/`run`: `_start_follower` is the only reader. It starts a daemon
      thread running `follow_run(..., from_start=True)` into an `FdSink`. `main()`'s `finally`
      closes the run, then stops the thread with a 2 s join (no join on Ctrl-C). A renderer
      exception stays in the thread.
    - `status` gains `active:` (running runs with Controller liveness, non-terminal jobs with
      worker liveness or the drain wording, and a `follow:` line for each), or `active: none`.
    - `resume`'s exit-45 path prints `follow it: ...` on stderr.
  - `job`: `follow_command(runtime_root, root)`. The lifecycle-lock refusal and `--abandon`'s
    active-worker refusal (both exit 45) end with `Follow it: ...`. `_announce_orphaned_worker`
    takes `runtime_root` and prints the follow line. With `drained` (set by `on_group_drain`
    after its flush) it says `worker pid P exited; its process group G still has members
    running ...`.
  - Scope notes: `observe` calls `managed_repo._resolve_repository_root` and `worker`'s private
    `/proc` readers rather than widening either module. The existing Ctrl-C test in
    `tests/test_job.py` now expects two lines, and the drain-aware Ctrl-C test sits beside it.
    `follow` does not handle `BrokenPipeError` on stdout yet (CP7's detach test). A
    devnull-`dup2` would trip the write-containment scan.
  - Verified: the narrow set `tests.test_observe tests.test_cli tests.test_package_structure`
    passed (156 tests), and `tests.test_job tests.test_resume tests.test_write_containment
    tests.test_lock` passed (226). The full suite passed (1181 tests, 6 skipped). The CLI tests
    include a real source-checkout `step` followed by a bare `follow <repo>` with no
    `--runtime-dir` (ladder row 3, runtime tree unchanged), and a child-process
    `step --follow` whose exit code, stdout and job status match a plain step.

**Next action:** `/milestone-implement workflow-controller-release-runtime-observability` for the
next ready checkpoint (CP7 or CP8).
