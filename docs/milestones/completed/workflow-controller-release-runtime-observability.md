# Archived milestone narrative — `workflow-controller-release-runtime-observability`

Archived verbatim from `docs/ACTIVE_MILESTONE.md` on 2026-09-24, as part of
`/accept-milestone`'s own step 5, at this work item's own acceptance. This
file's content below this notice is unedited from the version
`docs/ACTIVE_MILESTONE.md` carried immediately before this acceptance
(functional review: checklist evidence commit `c1272630517120cc1a94793f0c6d1ff76b14ec9b`,
overall **PASS**, round 1; the optional live flow 9 was waived; the
non-blocking findings are listed in `docs/ACTIVE_MILESTONE.md`'s completion
status, not here).

**Not archived here, deliberately** (same reasoning the prior archived
milestones' own files already state for their own milestones):
`docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md`, its registry
(`docs/ai-workflow/registry/workflow-controller-release-runtime-observability-registry.json`),
and its requirements mapping
(`docs/ai-workflow/requirements/workflow-controller-release-runtime-observability-mapping.json`)
all remain at their original paths, unmoved and unmodified —
`docs/ai-workflow/WORKFLOW_STATE.json`'s own
`work_items["workflow-controller-release-runtime-observability"]` entry still
declares these exact paths as its `plan_path`/`registry_path`/
`mapping_path`, and `plan_approval.review_content_manifest` pins their blobs
at these same paths, so moving any of them would make that historical
approval record's own manifest unresolvable. The
`workflow-controller-release-runtime-observability` entry in
`docs/ai-workflow/WORKFLOW_STATE.json` (`work_items` map, phase
`MILESTONE_COMPLETE`) and the full Git history of its approvals are
likewise untouched by this archival.

---

# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-release-runtime-observability`, governing
workflow version `2.2`, base commit `16c3fb4670bfeb9c7ce82aaff13f5a643a3f2400` (the acceptance
commit of `workflow-controller-automatic-lifecycle-orchestration`). Plan revision 6 was approved by
both plan-review stages (approval commit `3397e39`). Implementation revision 1 was technically
approved (commit `0e8cecb`). `docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for the
phase and for each checkpoint's status. See "Functional review checklist" at the end.

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

- **CP7 -- observation isolation and equivalence: complete.** No product code changed.
  - New `tests/test_observation_equivalence.py`. Every Controller is a real `python -m controller`
    process run from a committed `fixtures.build_checkout` clone. It pins and materialises for
    real, owns a real fd 2, and exits through real interpreter finalisation. The worker is the
    scripted fake, driven by `test_lifecycle_orchestration.Lifecycle` (imported, not factored
    out). Commit dates are fixed (`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`), so identical fixtures
    have identical Git histories. Ids, timestamps, pids and fixture paths are replaced by
    placeholders; everything else is compared exactly.
  - The scripted lifecycle has two legs. Leg 1 is `run` on a `"2.2"` `PLANNING` item:
    `/milestone-plan`, then `/review-plan`, then the manual-external plan gate (exit 10). Then
    the human's approval is performed in-process. Leg 2 is `run --max-steps 1`: one
    `/milestone-implement` checkpoint (exit 16).
  - Tests:
    1. equivalence: plain, `--follow`, and plain with a `follow` process attached after the
       first event and `SIGKILL`ed mid-job. Equal exit codes, stdout, run records and logs, job
       records, logs and worker streams, target state, `git log`/`git status`, and each worker's
       argv, cwd, env keys and fd count;
    2. attach: a paused worker, a second-process `follow <repo>` prints the emitted events, then
       the post-release ones, and exits 0 when the run ends;
    3. detach: one follower `SIGKILL`ed and one whose stdout reader is already closed. The job is
       `FINISHED`, exit 0, and the results equal a plain step's;
    4. `run --follow` with a closed stderr reader: exit code and results equal the plain run;
    4a. an unread stderr pipe and a stream that renders larger than the pipe: bounded, not
       signalled, results equal. In the `ControllerError` variant, a hold file keeps the second
       worker waiting until `FIONREAD` shows the renderer has filled the pipe down to its
       reserve. The single `error:` line must then land after more than 32 KiB of rendering.
       The renderer's lines are under 100 bytes, so without the reserve that line would block;
    5. a streamed job's `worker`, `worker_outcome`, `transition_verified` and `status` equal
       those of a result-only stream;
    - end to end, `follow` (bare, `--run`, `--job`) leaves the runtime root and target
      unchanged (type, size, mtime), and a legacy single-JSON `worker.stdout` with no
      `worker_streams` replays as its `worker result` line.
  - `tests/fake_claude.py` gains `FAKE_CLAUDE_DIAG_LOG`, which appends one line per invocation
    (argv, cwd, sorted env keys, fd count). `FAKE_CLAUDE_DIAG_FILE` keeps only the last worker.
  - Mutation checks, each reverted afterwards: a follower-written run event fails test 1; a
    `follow` that touches the runtime root fails the zero-write test; an `FdSink` without the
    poll/reserve check hangs the `ControllerError` variant past its 30 s bound; a renderer
    writing through buffered `sys.stderr` fails tests 4 (exit 120) and 4a (hang).
  - **Open for the reviewer:** a `follow` whose stdout reader is gone dies with a
    `BrokenPipeError` traceback and exit 120 (CP6 left this to CP7). The worker and the run are
    unaffected, which is all test 3 asserts. The plan says `follow` uses only `0`/`2`/`20`
    (ADR 0002, CP10), but it names no code for a broken stdout. CP7 is test-only, so the
    behaviour is recorded here, not changed.
  - Verified: the narrow set `tests.test_observation_equivalence tests.test_lifecycle_orchestration
    tests.test_lock tests.test_routing tests.test_resume tests.test_golden_plan_stage_decisions`
    passed (188 tests). The new module passed 5 sequential runs and 4 concurrent ones.
    `tests.test_worker tests.test_job tests.test_cli tests.test_observe
    tests.test_package_structure` passed (332) after the `fake_claude` change.

- **CP8 -- release tooling: complete.**
  - New stdlib-only `tools/release.py`, which puts the repository root on `sys.path` and imports
    `controller.buildinfo` and `controller.version`. Every refusal exits `1` with one stderr line,
    `release.py <command>: refused: <check>: <detail>`. The subcommands:
    - `version`;
    - `verify-tag TAG`: checks `tag format` and `tag/version`, and the `pyproject version source`
      check requires dynamic `version`, no static `version`, and
      `attr = "controller.version.__version__"`;
    - `verify-wheel WHEEL (--tag TAG | --local) --commit SHA`. Its checks, in order: `wheel filename`,
      `wheel metadata` (`Name`, `Version`), `entry point` (parsed `console_scripts`),
      `required files`, `forbidden files` (`SOURCE_PIN.json`, `__pycache__`, `*.pyc`), `build info`,
      `package digest` (recomputed from the wheel's `controller/` member bytes), `source commit`,
      `source dirty` and `build origin`;
    - `check-unpublished TAG`: runs `gh release view TAG --json tagName` and passes only when it
      exits non-zero with a stderr line that is exactly `release not found`. Exit 0 is refused as
      `already published`. Anything else, including `gh` being absent, is refused as
      `release lookup undecidable`;
    - `verify-tag-commit TAG SHA`: runs `git ls-remote origin refs/tags/TAG refs/tags/TAG^{}` and
      uses the peeled line when there is one. A different commit is refused as `tag moved`. A
      non-zero exit, no direct line, a malformed line, an unexpected or duplicated ref, or `git`
      being absent is refused as `tag commit undecidable`;
    - `checksums DIR`: covers regular files only, sorted, in `sha256sum` format. `SHA256SUMS` is
      excluded by name, and the file is written atomically.
  - `controller/buildinfo.py`: the canonical digest form is now `digest_of_file_hashes`, which
    `compute_package_digest` and `verify-wheel` share. This is a refactor with no digest change.
  - **Defect fixed in CP1 code:** `buildinfo`'s `SEMVER_RE`/`_COMMIT_RE`/`_DIGEST_RE` checks used
    `.match` with a `$` anchor, which also matches before a trailing newline. So
    `version_for_tag("v1.1.0\n")` returned `"1.1.0\n"`, and a `source_commit` or `package_digest`
    with a trailing newline validated. All five sites now use `.fullmatch`, and
    `tests.test_buildinfo` covers each one.
  - New `tests/test_release_tools.py`. `verify-wheel` runs against a local wheel and a release
    wheel, both built once per class from a committed `build_checkout` clone, and against copies
    of them mutated in-test. `gh` and Git are injected runners. The test covers every plan case,
    plus the one-line CLI refusal and `version`.
  - Mutation checks, each reverted afterwards, were all caught: skipping the digest comparison;
    ignoring the peeled line; treating any `gh` failure as not found; not excluding
    `SHA256SUMS`; and reverting `fullmatch` in `buildinfo`.
  - Verified: `tests.test_release_tools tests.test_buildinfo` passed (67 tests,
    `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, no skips). `tests.test_identity tests.test_runtime
    tests.test_package_structure` passed, and so did `tests.test_packaged_runtime` (9).

- **CP9 -- GitHub Actions: complete.**
  - New stdlib-only `tools/ci_workflows.py` holds the three workflows as Python data plus a
    deterministic emitter. Mappings keep insertion order, lists become block sequences and
    multi-line strings become `|` blocks. A string is bare only when `SAFE_SCALAR_RE` (a leading
    letter or `_`, then `[A-Za-z0-9_./@-]`) accepts it and it is not a YAML 1.1 reserved word;
    anything else is JSON-quoted. So `on` renders as the key `"on":`. `--write` renders
    `.github/workflows/{validate,ci,release}.yml`. `--check` exits `1` naming each file that
    differs.
  - `validate.yml` (`workflow_call` only) has three jobs: `controller`, a 7-shard matrix;
    `conformance`, the 7 frozen suites; and `package`. `package` pins `setuptools>=70.1`, builds
    the wheel, runs `verify-wheel --local` with a peeled `$GITHUB_SHA` and
    `tests.test_packaged_runtime` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, and ends with a
    pipx smoke test that checks `--version` line 1. Every job is `ubuntu-latest` and
    `contents: read`, and every matrix sets `fail-fast: false`.
    `tests.test_integration_disposable_repo` is excluded by name, with the reason recorded in the
    model.
  - `ci.yml` runs on `push: main` and on `pull_request`. Its concurrency group is
    `${{ github.workflow }}-${{ github.ref }}` with cancel-in-progress, and it calls
    `validate.yml`. `release.yml` follows the plan's steps: `v*` trigger, non-cancelling
    per-ref concurrency, then `validate`, `build` and `publish`. `publish` is the only job with
    `contents: write`, and its job-level `env` sets `GH_TOKEN` and `RELEASE_COMMIT`. Every action
    in `release.yml` is pinned to a 40-hex commit, with its tag in a trailing comment. The
    commits were read with `git ls-remote` on 2026-09-24: checkout v4, setup-python v5 and
    upload/download-artifact v4, all lightweight tags. `publish` also runs `setup-python 3.12`
    after checkout (`tools/release.py` needs `tomllib`). This is an addition to the plan's step
    list; the ordering the plan requires is unchanged.
  - **Recorded sources:**
    - `$GITHUB_SHA`: GitHub's "Events that trigger workflows" page gives it for `push` as the
      "Tip commit pushed to the ref", so an annotated tag push should carry the commit. The peel
      is kept regardless.
    - `gh release create`: with asset arguments it creates a draft, uploads the assets and then
      publishes. The gh manual says so, and so does `draftWhileUploading` in
      `cli/cli` `pkg/cmd/release/create/create.go`. That was confirmed, so `publish` uses the
      single-command form and there is no `gh release edit`.

    Both notes are in the model (`GITHUB_SHA_NOTE`, `GH_RELEASE_CREATE_NOTE`) and in
    `release.yml`'s header.
  - Deleted `.github/workflows/controller-tests.yml`. `workflow-conformance.yml` is untouched: a
    test checks that it still matches its managed digest.
  - New `tests/test_ci_workflows.py` (44 tests) covers every plan case: `--check` passes and a
    flipped byte fails it; the emitter golden strings, quoting and the `on` key; the PyYAML
    round-trip (it runs here because PyYAML is importable); shard coverage; the conformance
    matrix; the triggers, concurrency, gating, permissions and pinning; the build and publish
    step order; the peel and its job output; each step's effective `GH_TOKEN`/`RELEASE_COMMIT`;
    no bare `"$GITHUB_SHA"` passed to `--commit`; no forbidden `gh release` forms; and the
    artifact contract.
  - Mutation checks, each reverted afterwards, were all caught: a cancelling release
    concurrency, `GH_TOKEN` dropped from `publish`, a shard dropped, and a mismatched upload
    `path`.
  - Verified: `tests.test_ci_workflows` passed (44), and so did
    `python3 tools/ci_workflows.py --check`. `tests.test_plan_document_consistency
    tests.test_package_structure tests.test_checklist_corrections` passed (55).

- **CP10 -- operator documentation: complete.** Documentation and one test module only; no product
  code changed.
  - `README.md`:
    - "Installation" is rewritten: pipx install of the release wheel, verification with
      `SHA256SUMS`/`sha256sum -c` and `--version`, upgrade (check `active: none` first; same
      generation continues, a new generation stops `run` with exit `50`, and the two fail-closed
      `SourceSnapshotError` cases, venv recreation and a Python minor-version change), rollback
      (newer-generation records and `GenerationHandoffPendingError`) and the editable development
      install. The old "Only a Controller installed from its own checkout ..." paragraph is gone.
    - New sections: "Runtime identity" (the three kinds, `BUILD_INFO.json`, `--version`/`status`
      text, `controller_runtime`, what `build_origin: "release"` does not prove, version versus
      generation), "Observing workers" (the log files and their `0o600` mode, `step/run --follow`,
      `follow`, `status` `active:`, no thinking blocks, presentation-only), "Continuous
      integration" (the `controller`/`conformance`/`package` jobs, `ci.yml`, the generated files,
      the branch-protection note for the removed `controller-tests` check) and "Releasing" (the
      five steps, the moved-tag rule and its seconds-long window, concurrency).
    - "Controller-owned runtime state" now lists the four ladder rows, row 3 source-only, and the
      old `site-packages/.controller` path that can be deleted. The CLI table gains `follow`,
      `--follow` and `--version`.
  - New `docs/adr/0002-release-runtime-identity-and-observability.md`: every scope judgment with
    its rejected alternative, and `follow`'s `0`/`2`/`20` table. ADR 0001 is unchanged.
    **For the reviewer:** ADR 0002 records CP7's open item as open: a `follow` whose stdout reader
    is gone dies with a `BrokenPipeError` traceback (exit `120`), outside those three codes. CP10
    changes no code, so it states the behaviour rather than hiding it.
  - `tests/test_plan_document_consistency.py`: `follow` is now a recognised command name, so
    README `follow` lines are parsed by the live parser (the Gen-1 plan's recognised-line count is
    still 7). ADR 0002's invocation lines are checked. A new `ReadmeValidateJobTest` reads the job
    ids from `tools/ci_workflows.py`'s `validate_workflow()` and requires each to appear in the
    README as a code span, with a negative case.
  - Verified: `tests.test_plan_document_consistency tests.test_checklist_corrections` passed (47
    tests).

- **CP11 -- full verification: complete.** No code changed. The drill scripts are throwaway files
  under `/tmp`, not committed.
  1. `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest discover -s tests -t . -v` (no
     `PYTHONPATH`): **Ran 1274 tests in 96.5 s, OK (skipped=6)**. All six skips are the
     `CONTROLLER_LIVE_WORKER=1` opt-ins in `tests.test_integration_disposable_repo`.
  2. The seven conformance suites, from `scripts/`, all passed (timings under step 7).
  3. `python3 tools/ci_workflows.py --check`: exit `0`.
  4. **Packaged drill, real pipx 1.15.0**, isolated with `PIPX_HOME`/`PIPX_BIN_DIR`/
     `PIPX_MAN_DIR` and `XDG_STATE_HOME` in a temporary directory. The operator's own
     `~/.local/share/pipx/venvs/workflow-controller` was untouched. The worker was
     `tests/fake_claude.py`, the Manager was the test stub, and the target was the
     `test_packaged_runtime` `PLANNING` fixture.
     - Base commit `16c3fb4`: wheel `workflow_controller-1.0.1`. `pipx install`, then `step`
       gave **exit `20`**, `error: git archive failed: fatal: not a git repository (or any parent
       up to mount point /)`. That reproduces the defect.
     - HEAD `d33840d`: wheel `workflow_controller-1.1.0`, pipx-installed, clone then moved away.
       - `--version` printed `workflow-controller 1.1.0` / `runtime: package (local build from
         d33840df1350)`.
       - `status` printed `controller: workflow-controller 1.1.0 -- package (local build from
         d33840df1350)` and `no Controller runtime state at <xdg>/workflow-controller (ladder
         row 4)`.
       - `inspect` exited `0`.
       - `step` exited `0`, and the target moved to `AWAITING_LOCAL_PLAN_REVIEW`.
       - The job record is `FINISHED`/`SUCCESS`, with `controller_source_commit` `d33840d...`.
       - Its `controller_runtime` is `{runtime_kind: package, source_kind: package, version:
         1.1.0, generation: 1, source_commit: d33840d..., build_origin: local, release_tag: null,
         package_digest: cd9011ed..., tree_digest: d5982c90...}`.
  5. **Real-CLI stream check:** Claude Code `2.1.281`, `claude -p "Reply with the word ok."
     --output-format stream-json --verbose --model haiku`, exit `0`. The stream has five events
     (`system`/`init` on `claude-haiku-4-5-20251001`, `assistant` x2, `rate_limit_event`,
     `result`). `_parse_worker_stream` returned the `result` event (`result: "ok"`, `is_error:
     false`), and `_classify` gave **`SUCCESS`**.
  6. **Live drill, run once, real spend:**
     - The target was `_seed_target` from `tests.test_integration_disposable_repo`: a real
       Workflow `2.5.1` `full` install with the trivial `hello-file` milestone.
     - The command was `run --follow --max-steps 1`, default routing and default permission mode
       (`auto`), from the source runtime with `--runtime-dir`. `--max-steps 1` limits the spend
       to the one `/milestone-plan` worker.
     - A second `follow <target>` process attached 15 s after `worker_spawned`. It was
       `SIGKILL`ed 25 s later with the worker still running, then re-attached 5 s later.
     - Results:
       - Run `20260924T110338Z-ba362f56` ended with exit `16` (`EXIT_MAX_STEPS`, as expected)
         after 122 s.
       - Job `20260924T110338Z-cbed733d` is `FINISHED`, worker `SUCCESS`,
         `transition_verified: true`, `__NO_PHASE__ -> AWAITING_LOCAL_PLAN_REVIEW`.
       - Worker session `a750ce64-...`, model `claude-opus-5-5[1m]`, permission `auto`.
       - 23 turns, cost $0.967, 119.4 s.
     - The real stream (109 events) **passes the strict parser**.
     - **Tool calls and results render**: the `--from-start` replay has 22 `worker tool Bash:`
       lines and 22 `worker result Bash ok:` lines.
     - **No thinking block is rendered.** The raw `worker.stdout` holds 16 events with thinking
       blocks, kept as evidence, and none of the four renderings (run stderr, both follows, the
       replay) contains the word "thinking". `worker.stdout` and `events.jsonl` are `0o600`.
     - The killed follow exited `-9` with no effect on the run. The re-attached follow printed
       the replay, followed live and **exited `0`** when the run ended, printing `run ended:
       exit 16`.
     - `follow --run 20260924T110338Z-ba362f56 --from-start` exited `0` with 504 lines,
       identical in length to the run's own `--follow` stderr (504 lines).
     - Observation: `rate_limit_event` renders as `worker unknown event rate_limit_event`. That
       is harmless and presentation-only, but it is noise; see "For the reviewer" below.
  7. **Per-shard timings**, from one local run of each shard's command (host: this machine, run
     sequentially). Each shard exited `0` with `OK`:

     | Shard | Tests | Time |
     |---|---|---|
     | controller (identity) | 141 | 10.1 s |
     | controller (job) | 211 | 19.0 s |
     | controller (resume) | 90 | 2.5 s |
     | controller (decision) | 392 | 2.2 s |
     | controller (cli) | 114 | 12.7 s |
     | controller (worker) | 141 | 20.7 s |
     | controller (docs) | 158 | 9.2 s |
     | package (`tests.test_packaged_runtime`, `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`) | 9 | 20.4 s |
     | conformance `workflow_fingerprint_test.py` | 218 | 1.8 s |
     | conformance `workflow_state_test.py` | 853 | 23.1 s |
     | conformance `workflow_test_harness_test.py` | 19 | 0.1 s |
     | conformance `workflow_integration_test.py` | 260 (skipped 1) | 1.9 s |
     | conformance `workflow_acceptance_matrix_test.py` | 146 (skipped 18) | 75.8 s |
     | conformance `workflow_state_completion_obligations_test.py` | 106 | 4.0 s |
     | conformance `workflow_fingerprint_generalization_test.py` | 79 | 4.2 s |

     The Controller shards plus the package shard total 1256 tests. Adding the 18 tests of the
     excluded `test_integration_disposable_repo` gives the full-suite count of 1274, so every
     module is covered.
  - **For the reviewer:** the `stream-json` CLI emits `rate_limit_event` lines. The strict parser
    accepts them, since they are JSON objects, and the renderer shows them as an "unknown event"
    line. No change was made in CP11: this is a verification checkpoint, and the plan does not
    list the event kind.

## Self-review (`SELF_REVIEWING_IMPLEMENTATION`)

The phase was already `SELF_REVIEWING_IMPLEMENTATION` (written by CP11's `complete_checkpoint`), so
`enter_self_reviewing_implementation` was a no-op (`state_revision` stayed at 42) and there was no
state commit. The review covered the full diff from `16c3fb4` to `be6a934`: every product module
(`buildinfo`, `version`, `identity`, `runtime`, `handoff`, `worker`, `job`, `observe`, `cli`),
`setup.py` and `tools/release.py`/`tools/ci_workflows.py`. No Blocking findings. One Important
finding was fixed with a regression test, and one Minor finding was fixed. One Minor finding was
left as it is.

- **I1 (Important, fixed):** `follow` with a stdout whose reader is gone (`follow | head`) died with
  a `BrokenPipeError` traceback and exit `120`, outside the plan's `0`/`2`/`20` contract. This was
  CP7's open item, which ADR 0002 recorded. `cmd_follow` now treats it like Ctrl-C: the operator
  stopped reading, so it exits `0`. It drops `sys.stdout` so the interpreter's final flush of the
  unwritable buffer cannot raise again. It does not `dup2` `/dev/null` over the descriptor, which
  the write-containment scan would flag. The "nothing active" line now goes through the same
  flushing sink, so a broken pipe there is caught too. `tests.test_cli` gains
  `test_a_stdout_whose_reader_is_gone_exits_0_without_a_traceback`, a real subprocess, since only
  the interpreter's exit shows it. It covers both `--run --from-start` and a bare `follow`. A
  negative control (the fix stashed) failed both subtests with exit `120`. CP7's detach test now
  asserts exit `0` and no traceback, where it used to assert only "not `0`". ADR 0002 and the
  README state the behaviour.
- **M1 (Minor, fixed):** `FdSink` decoded the `FIONREAD` count as little-endian. It now uses
  `sys.byteorder`, the kernel's native `int`.
- **M2 (Minor, left):** `rate_limit_event` renders as `worker unknown event rate_limit_event`
  (CP11's observation). The plan's presentation table maps any unlisted type to `unknown`, so
  suppressing or naming it would be a presentation change the plan does not make. It is left for
  the reviewer.

Verified after the fixes:
- `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest discover -s tests -t .` (no
  `PYTHONPATH`): **Ran 1275 tests in 97.1 s, OK (skipped=6)**. That is CP11's 1274 plus the new
  test.
- The seven conformance suites, from `scripts/`: 218, 853, 19, 260 (skipped 1), 146 (skipped 18),
  106 and 79 tests, all OK.
- `python3 tools/ci_workflows.py --check`: exit `0`.

Both implementation-review stages then approved implementation revision 1: local round 1 and
manual external round 1, against bundle `7e4f29e9...` and `review_content_id` `aa88a009...`. The
external reviewer left one optional finding (O1): where the drain cannot count the process
group's members, `status`, the follower heartbeat and `worker_exited` say `0 process(es)`, but the
stderr drain line says `an unknown number of processes`. Technical approval is commit `0e8cecb`.

## Functional review checklist

This checklist covers implementation revision 1, reviewed at `3549311`, with technical approval
`0e8cecb`. The automated state is current: at `3549311` the full suite ran 1275 tests, OK (6
opt-in live skips), and the seven frozen Workflow suites passed. Only `WORKFLOW_STATE.json` and
this file have changed since. Record findings in `.ai-review/feedback/FUNCTIONAL_REVIEW.md`.

Every flow below was dry-run before this checklist was committed, except the spend in flow 9.
Flows 1-8 cost nothing: the worker is `tests/fake_claude.py` and the Workflow Manager is the
offline test stub. Flow 9 is optional and live (about USD 1).

### Setup

1. `claude` and `workflow-manager` are on `PATH` (flow 9 only). `pipx` is installed.
2. In one zsh shell, used for every later step (under bash, replace `${pipestatus[1]}` with
   `${PIPESTATUS[0]}`):

   ```zsh
   C=/home/rodrigo/Workspace/workflow-controller; W=$(mktemp -d); unset PYTHONPATH
   export PIPX_HOME=$W/pipx PIPX_BIN_DIR=$W/bin PIPX_MAN_DIR=$W/man XDG_STATE_HOME=$W/xdg
   WC=$W/bin/workflow-controller
   seed() { (cd $C && python3 -c "import shlex; from pathlib import Path; from tests.test_packaged_runtime import _build_planning_target as b, _planning_worker_env as e; from tests.fixtures import write_stub_workflow_manager as m; w=Path('$W'); t=b(w/'t$1'); m(w/'workflow-manager'); open(w/'t$1.env','w').write(''.join(f'{k}={shlex.quote(v)}\n' for k,v in e(t, w/'inv$1').items()))") }
   fake() { local n=$1; shift; (set -a; . $W/t$n.env; set +a; cd /tmp; $WC --workflow-manager $W/workflow-manager --claude-binary $C/tests/fake_claude.py "$@") }
   ```

   The pipx variables isolate everything, so your own pipx install of `workflow-controller` is
   never touched. `XDG_STATE_HOME` puts the Controller's runtime root at
   `$W/xdg/workflow-controller` (ladder row 4). `seed N` builds a committed `"2.1"` `PLANNING`
   target at `$W/tN`, plus the fake worker's environment in `$W/tN.env`. With that environment,
   the fake worker performs `/milestone-plan`'s transition to `AWAITING_LOCAL_PLAN_REVIEW`.
   `fake N <args>` runs the installed Controller from `/tmp` with that worker and the stub
   Manager.
3. Build a local wheel from a clean clone of this commit, install it with pipx, then delete the
   clone:

   ```zsh
   git clone -q $C $W/clone && (cd $W && python3 -m pip wheel -q --no-deps --no-build-isolation --wheel-dir $W/dist $W/clone)
   pipx install -q $W/dist/*.whl && rm -rf $W/clone; H=$(git -C $C rev-parse --short=12 HEAD)
   ```

   Expected: one wheel, `$W/dist/workflow_controller-1.1.0-py3-none-any.whl`. `pipx install`
   succeeds, and `$W/clone` no longer exists.

### Test data

Only the throwaway `PLANNING` targets that `seed` creates under `$W`, one per flow that launches a
worker. Nothing in this repository is modified. `pip wheel` runs against the clone, so it writes
no `build/` directory into `$C`.

### Flows

1. **Runtime identity, with no checkout.** From `/tmp`, run `$WC --version`, then `$WC status`.
   Expected:
   - `--version` prints exactly two lines: `workflow-controller 1.1.0` and
     `runtime: package (local build from $H)`;
   - `status` opens with `controller: workflow-controller 1.1.0 -- package (local build from $H)`,
     then says `no Controller runtime state at $W/xdg/workflow-controller (ladder row 4)`. Exit
     `0`.
2. **The packaged runtime launches a worker (the `git archive failed` defect).** Run
   `seed 1; fake 1 step $W/t1; echo exit=$?`.
   Expected:
   - `exit=0`, and no `git archive` anywhere in the output;
   - the target's phase is now `AWAITING_LOCAL_PLAN_REVIEW`. To check it, run
     `python3 -c "import json; print(json.load(open('$W/t1/docs/ai-workflow/WORKFLOW_STATE.json'))['work_items']['wi-1']['phase'])"`;
   - the only job record, `$W/xdg/workflow-controller/jobs/*.json`, is `FINISHED`, with
     `worker_outcome` `SUCCESS` and `transition_verified: true`. Its `controller_runtime` is
     `runtime_kind: package`, `source_kind: package`, `version: 1.1.0`, `generation: 1`,
     `build_origin: local`, `release_tag: null`, and a `source_commit` that starts with `$H`;
   - `stat -c '%a %n' $W/xdg/workflow-controller/jobs/*/*` lists `worker.stdout`, `worker.stderr`
     and `events.jsonl`, each with mode `600`.
3. **`step --follow`.** Run
   `seed 2; FAKE_CLAUDE_EVENT_DELAY=1 fake 2 step --follow $W/t2 >$W/f2.out 2>$W/f2.err; echo exit=$?`,
   then `cat $W/f2.out $W/f2.err`.
   Expected:
   - `exit=0`, and `f2.out` is empty (the rendering goes to stderr only);
   - `f2.err` has about 15 time-stamped lines, each with a `run`/`job`/`worker` source column:
     `run ... started`, `step 1`, `job ... started`, `PLANNED (/milestone-plan wi-1)`,
     `LAUNCHED (..., milestone-plan, None/None)` (the route inherits the model and effort),
     `worker spawned: pid P, process group P`, `worker session fake-session-id ...`,
     `Working on it.`, `tool Bash: true`, `result Bash ok:`, `COMPLETED (worker SUCCESS, exit
     0)`, `FINISHED (PLANNING -> AWAITING_LOCAL_PLAN_REVIEW verified)`,
     `worker result: success, ... turns=1, cost=0.0 ...`, `job ... ended: FINISHED` and
     `run ended: exit 0`;
   - the worker lines arrive about a second apart, live, not all at the end.
4. **`status` while a worker runs, and `follow` from a second process.**
   1. Start a worker that pauses after 3 events:
      `seed 3; rm -f $W/rel3; FAKE_CLAUDE_PAUSE_AFTER_EVENTS_FILE=3:$W/rel3 fake 3 step $W/t3 >$W/s3.out 2>&1 & SP=$!`.
      Wait about 3 s.
   2. `(cd /tmp; $WC status)`. Expected: under `active:`, a `run ... (step, target $W/t3):
      controller pid ... active` line and a `job ... (LAUNCHED, target $W/t3): worker pid ...
      active` line. Each is followed by a `follow:` line, which is a complete
      `workflow-controller --runtime-dir $W/xdg/workflow-controller follow $W/t3` command.
   3. `(cd /tmp; $WC follow $W/t3) & FP=$!`. Expected: it replays the events so far at once
      (`run ... started` through `worker session`, `Working on it.` and `tool Bash: true`), then
      waits.
   4. `touch $W/rel3; wait $SP; echo step=$?; wait $FP; echo follow=$?`. Expected: the follower
      prints the rest, through `run ended: exit 0`, and then exits by itself. `step=0` and
      `follow=0`.
   5. `(cd /tmp; $WC status)`. Expected: `active: none`.
5. **`follow` when nothing is active, replay, `--json`, and refusals.** Set
   `R=$(basename $(ls $W/xdg/workflow-controller/runs/*.json | tail -1) .json)` (flow 4's run).
   All commands below run from `/tmp`.
   - `$WC follow $W/t3; echo exit=$?`: `nothing active for $W/t3; last run $R ended with exit 0;
     replay: workflow-controller ... follow --run $R --from-start $W/t3`, then `exit=0`;
   - `$WC follow --run $R --from-start $W/t3 | wc -l`: `15`, the same lines as flow 4's follower
     printed;
   - `$WC --json follow --run $R $W/t3 | head -2`: one JSON object per line, the first being
     `"event": "run_started"`, `"kind": "run_event"`, `"source": "run"`;
   - `$WC follow --run nope $W/t3; echo exit=$?`: `error: no readable run record 'nope' ...`,
     then `exit=20`;
   - `$WC follow --job $(basename $(ls -d $W/xdg/workflow-controller/jobs/*/ | tail -1)) $W/t1; echo exit=$?`:
     `error: job ... belongs to target '$W/t3', not $W/t1`, then `exit=20`.
6. **`follow` writes nothing, and a closed stdout.**
   - Run `find $W/xdg -printf '%T@ %s %p\n' | sort > $W/a; (cd /tmp; $WC follow --run $R --from-start $W/t3 >/dev/null); find $W/xdg -printf '%T@ %s %p\n' | sort > $W/b; cmp $W/a $W/b && echo unchanged`.
     Expected: `unchanged`.
   - Run `(cd /tmp; $WC follow --run $R --from-start $W/t3 2>$W/pipe.err | true); echo follow=${pipestatus[1]}; wc -c < $W/pipe.err`
     three times. Expected: `follow=0` and `0` bytes of stderr each time, with no
     `BrokenPipeError` (this was self-review I1). For contrast, a plain Python program shows the
     traceback it replaced:
     `python3 -c "import time; time.sleep(0.2); print('x'*100)" | true; echo ${pipestatus[1]}`
     prints `BrokenPipeError`, then `120`.
7. **A tampered install refuses.** Edit one installed byte, try a step, then restore:

   ```zsh
   SP=$(echo $W/pipx/venvs/workflow-controller/lib/python3*/site-packages)
   python3 -c "p='$SP/controller/cli.py'; d=bytearray(open(p,'rb').read()); d[3]=ord('X'); open(p,'wb').write(d)"
   seed 4; fake 4 step $W/t4; echo exit=$?; ls $W/inv4
   pipx uninstall workflow-controller && pipx install -q $W/dist/*.whl
   fake 4 step $W/t4; echo exit=$?
   ```

   Expected:
   - the first step prints `error: the installed package at $SP does not match its own build
     info: package digest <new> != recorded <old> -- it was modified after it was built, or
     partially upgraded`, then `exit=20`;
   - `ls $W/inv4` fails, because no worker ran. The number of `$W/xdg/workflow-controller/jobs/*.json`
     files is unchanged;
   - after the reinstall, the same step gives `exit=0`.

   Use `uninstall` + `install` here, not `pipx install --force`. With pipx's `uv` backend,
   `--force` on this machine fails with `A virtual environment already exists`.
8. **Release tooling, local only.** From `$C`:
   - `python3 tools/release.py verify-tag v1.1.0` prints `ok: v1.1.0 matches version 1.1.0`.
     `verify-tag v1.2.0` is refused with `tag/version`, exit `1`;
   - `python3 tools/release.py verify-wheel $W/dist/*.whl --local --commit $(git rev-parse HEAD)`
     prints `ok: ... verified`. With `--commit $(git rev-parse HEAD~1)` it is refused with
     `source commit`, and with `--tag v1.1.0` instead of `--local` it is refused with `build origin`
     (`got 'local'`);
   - a release build:
     `git clone -q $C $W/rclone && (cd $W && WORKFLOW_CONTROLLER_RELEASE_TAG=v1.1.0 python3 -m pip wheel -q --no-deps --no-build-isolation --wheel-dir $W/rdist $W/rclone)`,
     then `verify-wheel $W/rdist/*.whl --tag v1.1.0 --commit $(git rev-parse HEAD)` prints `ok`.
     After `python3 -m venv $W/rv && $W/rv/bin/pip install -q --no-deps --no-index $W/rdist/*.whl`,
     running `(cd /tmp; $W/rv/bin/workflow-controller --version)` prints
     `runtime: package (release v1.1.0; built from $H; package <12 hex>)`;
   - `mkdir $W/sums && cp $W/rdist/*.whl $W/sums/ && python3 tools/release.py checksums $W/sums && (cd $W/sums && sha256sum -c SHA256SUMS)`
     prints `workflow_controller-1.1.0-py3-none-any.whl: OK`;
   - `python3 tools/release.py check-unpublished v1.1.0` prints
     `ok: no GitHub Release exists for v1.1.0`. This needs `gh` authenticated. Any other `gh`
     failure is refused as `release lookup undecidable`;
   - `python3 tools/ci_workflows.py --check` exits `0`.
   - **Optional, needs a push:** after this milestone lands on `main`, the `CI` workflow on
     GitHub runs the 7 `controller` shards, the `conformance` matrix and `package` in parallel,
     and a failing shard does not cancel the others. Do not push a `v*` tag as part of this
     review. That publishes a real Release.
9. **Optional, live, real spend (about USD 1): a real worker through the installed package.**
   Run
   `(cd $C && python3 -c "from pathlib import Path; from tests.test_integration_disposable_repo import _seed_target as s; s(Path('$W/live'))")`,
   then `(cd /tmp; $WC explain $W/live)`. That prints `next automatic action: /milestone-plan`.
   Then `(cd /tmp; $WC run --follow --max-steps 1 $W/live); echo exit=$?`. After about 15 s, from
   a second terminal with the same `W`/`WC`/`XDG_STATE_HOME`, run `$WC follow $W/live`, and
   Ctrl-C it partway through.
   Expected:
   - `exit=16` (the step limit). The job is `FINISHED` and the target is at
     `AWAITING_LOCAL_PLAN_REVIEW`;
   - the rendering shows `worker tool Bash: ...` and `worker result Bash ok:` pairs, and never a
     thinking block;
   - Ctrl-C ends the second `follow` with exit `0`, and the run carries on to completion.

   The dry run for this checklist seeded the target and ran `explain`, but did not launch the
   worker. CP11 step 6 ran the same live flow from the source runtime.

### Known limitations and out of scope

- **Line order across sources.** A job's `COMPLETED`/`FINISHED` lines can print before the worker's
  last one or two stream lines. The follower reads each file in turn, not by timestamp. Only
  `job ... ended` and `run ended` are guaranteed to come after all of that job's lines (flows 3
  and 4 show this).
- `rate_limit_event` from the real CLI renders as `worker unknown event rate_limit_event` (M2, left
  for review).
- The external reviewer's optional O1, above: an uncountable process group shows as `0 process(es)` in `status`, the heartbeat and
  `worker_exited`, but as `an unknown number of processes` on the stderr drain line.
- `step` prints nothing to stdout without `--json`; with `--follow` the rendering goes to stderr.
- There is no real GitHub Release in this review: the release workflow's tag, publish and
  duplicate-refusal path is covered by `tests.test_ci_workflows`/`tests.test_release_tools` and
  by flow 8's local commands, not by a published tag.
- No log retention or pruning. The carried-over follow-ups O1-O3 from the previous milestone
  remain out of scope.
