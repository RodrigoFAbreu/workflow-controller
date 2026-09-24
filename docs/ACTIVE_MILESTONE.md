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

**Next action:** `/milestone-implement workflow-controller-release-runtime-observability` for CP3.
