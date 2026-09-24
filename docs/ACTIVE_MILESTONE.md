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

**Next action:** `/milestone-implement workflow-controller-release-runtime-observability` for CP2.
