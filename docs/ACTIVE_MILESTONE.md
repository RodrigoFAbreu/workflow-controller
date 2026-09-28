# Active Milestone

## Milestone

`workflow-controller-workflow-2-6-integration`: admit Workflow 2.6.0 beside 2.5.1, minimally
(`docs/ROADMAP.md` section 1.6, step 1 of "At a glance"). It ships as Controller 1.3.0.

- Plan: `docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md`, revision 7, approved at
  `9fceff1` (`EXTERNAL_APPROVE`).
- Registry: `docs/ai-workflow/registry/workflow-controller-workflow-2-6-integration-registry.json`
  (CP1-CP6).
- Governing workflow version: `2.2`. Base commit: `2296ad6`. The milestone runs, and is accepted,
  under this repository's installed Workflow 2.5.1 (plan Decision 2).
- Ground truth for phase and checkpoint status: `docs/ai-workflow/WORKFLOW_STATE.json`.

The previous milestone, `workflow-controller-adaptive-test-sharding`, is complete. Its narrative
is archived at `docs/milestones/completed/workflow-controller-adaptive-test-sharding.md`.

## Goal

Let the Controller drive a target that runs the released Workflow 2.6.0, while every 2.5.1 target
keeps exactly today's behaviour. For 2.6+ targets the feedback path and the plan-review
publication status come from Workflow's own queries, run from a private copy of the digest-checked
script bytes. Every Workflow-derived inventory and decision golden is proven against each admitted
release, the 2.5.1 → 2.6.0 migration is proven, E1-E5 are answered (`merge_trunk` stays unwired),
and the result is released as 1.3.0.

## Checkpoint progress

| id | status | commit |
| --- | --- | --- |
| CP1 -- vendored release trees, one phase list, per-release inventories | complete | this checkpoint's commit |
| CP2 -- the Workflow contract module and the two query clients | not started | |
| CP3 -- release-aware feedback resolution | not started | |
| CP4 -- plan-review publication status and per-release writer declarations | not started | |
| CP5 -- admission of 2.6.0 and 2.5.1 → 2.6.0 migration | not started | |
| CP6 -- documentation, release 1.3.0 and full verification | not started | |

### CP1 -- vendored release trees, one phase list, per-release inventories (complete)

- **Vendored trees.** `tests/workflow_releases/2.5.1/` and `tests/workflow_releases/2.6.0/` each
  hold the seventeen `.claude/commands/*.md` files, `scripts/workflow_state.py`,
  `scripts/workflow_fingerprint.py` and `scripts/prepare-ai-review.sh`, at their target paths and
  with the Manager manifest's executable flags (`prepare-ai-review.sh` and
  `workflow_fingerprint.py` are executable). Each `RELEASE.json` records the release, the Manager
  source (`distribution/workflow/<release>/payload`), the Manager commit
  (`136c41722d2174327ad9299250bda0a3c91c0220` for both; the Manager's working tree of each
  release directory was verified equal to that commit), and each file's `sha256` and executable
  flag. The trees total 2.8 MB. They hold no `__init__.py` and no `test_*.py`.
- **`tools/workflow_releases.py`** (new, stdlib only, writes only under
  `tests/workflow_releases/`).
  - `sync <release> --from <distribution/workflow dir> [--manager-commit <sha>]` copies the subset
    and writes `RELEASE.json` from the release's `manifest.json`. It refuses when the release
    directory in the Manager's working tree differs from the recorded commit (tracked or
    untracked), when a payload file's `sha256` differs from the manifest's, and a release name
    that is not a dotted version.
  - `check` reports every recorded file whose `sha256` or executable bit differs, any file the
    record does not name (so a planted `scripts/uuid.py` is caught), a path outside the subset,
    and an admitted release (`managed_repo.VALIDATED_WORKFLOW_RELEASES`) with no tree. It exits 1
    naming each problem.
  - `manifest_mismatches(release, distribution)` compares a tree's record with the Manager
    manifest's subset.
- **Fixtures** (`tests/fixtures.py`).
  - New: `workflow_release_tree(release)` and `workflow_release_files(release)`.
  - New: `install_workflow_release(root, release, *, profile="full")`. It copies the vendored
    files with their modes and writes `installation.json` through `write_installation_manifest`.
  - New: `evaluate_in_workflow_release(release, expression)`. It reads a release's own
    `workflow_state` in a fresh `sys.executable -B -E -s -c` interpreter, with the vendored
    `scripts/` as `cwd`.
  - `copy_real_commands_dir`, `build_workflow_line_fixture` (now copying both Python scripts) and
    `write_stub_workflow_manager` take `release`, defaulting to
    `managed_repo.REFERENCE_WORKFLOW_RELEASE`. Every existing call site behaves as before.
  - `REAL_COMMANDS_DIR` (this repository's own `.claude/commands/`) is removed.
  - `build_target_managed_repository`'s placeholder `workflow_version="2.3.1"` is now the
    reference release. No golden contains `2.3.1`.
- **One phase list.** `decision.KNOWN_PHASES` carries the per-group comments, and
  `decision.TERMINAL_PHASES` is new there, moved from `target_state`. `target_state` re-exports
  both, so `DEPENDENCY_ORDER` is unaffected. `test_decision` pins the re-export with `assertIs`,
  and its hand-copied twenty stay as the independent witness.
- **Per-release inventories.** Each iterates `sorted(VALIDATED_WORKFLOW_RELEASES)` with
  `subTest(release=...)`, against the vendored tree, and is still `{"2.5.1"}` until CP5:
  - the phase set and the terminal set (`test_target_state.KnownPhaseSetEqualityTest`, one
    subprocess per release);
  - the command-file partition and the user-only derivation (`test_decision`);
  - property 5 against the table (`test_job_validation`). It uses `job.EXPECTED_OUTCOMES` until
    CP4 adds `expected_outcomes_for(release)`. The single-row property-5 tests read the reference
    tree.
  - `test_evidence`'s provenance-recovery phase check reads each admitted release the same way,
    beyond the plan's four. It is the other test that imported this repository's
    `scripts/workflow_state.py` in process. Both releases give the same three phases.
- **Readers moved off this repository's tree.** Through the fixture defaults or directly:
  `test_decision`, `test_evidence`, `test_job_validation`, `test_resume`, `test_target_state`,
  `test_packaged_runtime`, `test_trunk_orchestration_e2e`, and
  `test_integration_disposable_repo`'s fallback seed. That seed now calls
  `install_workflow_release` instead of copying this repository's `.claude/commands/`,
  `scripts/` and `.workflow-manager/`. It still copies `docs/ai-workflow/`.
- **Release-agnostic real-Manager admission.**
  `test_managed_repo.test_real_workflow_manager_admits_this_repository` asserts that the
  inspected release is admitted and equals this repository's `installation.json` release, not a
  literal. No test now asserts this repository's own release as a literal.
- **`tests/test_workflow_releases.py`** (new, 16 tests; 15 in CI, where the Manager-manifest class
  skips). It covers:
  - `check` passes (in process and as a CLI), and every admitted release has a tree;
  - each tree is exactly the subset, with `prepare-ai-review.sh` executable, and nothing is
    discoverable as a test;
  - each damaged-copy case (a byte, the executable bit, a missing file, a planted
    `scripts/uuid.py`, a missing admitted tree) is reported;
  - this repository's installed release is admitted, and its command files and three scripts
    equal that release's vendored tree, bytes and executable bit;
  - with a local Manager distribution (`WORKFLOW_MANAGER_DISTRIBUTION`, or the sibling checkout),
    both trees' records equal the Manager manifests;
  - `sync` against a synthetic Manager git checkout: the subset, modes, record, full commit,
    stale-file removal, and each refusal.
- **Verification.**
  - `python3 tools/workflow_releases.py check`: exit 0.
  - The golden files are byte-unchanged. `generate_external_implementation_review_decisions.py
    --check` and `generate_no_policy_lifecycle.py --check` report current.
    `tests.test_golden_plan_stage_decisions` passes.
  - Named modules: `tests.test_workflow_releases`, `test_decision`, `test_target_state`,
    `test_managed_repo` (with `workflow-manager` on `PATH`; the real admission test ran),
    `test_evidence`, `test_package_structure`, `test_write_containment`,
    `test_integration_disposable_repo`, `test_job_validation`, `test_resume`,
    `test_trunk_orchestration_e2e`, and `test_packaged_runtime` with
    `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`.
  - Full sharded run, `python3 tools/run_tests.py`, in the foreground: 2088 tests in 8 shards,
    PASS, exact coverage, 93.6 s wall.
  - Environmental: this session runs as a Controller-launched worker, a child subreaper, so
    orphans linger as zombies. Run bare, `tests.test_resume`'s `OrphanWorkerTest`,
    `UnreconcilableOrphanTest`, `EndToEndInterruptionTest` and
    `BootstrapEndToEndInterruptionTest` fail their orphan-reaped assertions. Under a reaping
    subreaper wrapper they pass (4 tests, 1.6 s). The full sharded run above ran under that
    wrapper. CP1 touches none of the code they exercise.
- **Notes for later checkpoints.**
  - `generate_plan_stage_decisions.py --check` exits 1 at the base commit as well as at CP1. Every
    `AMENDING_PLAN` case now derives the uniform decline reason, which
    `tests.test_golden_plan_stage_decisions` reverts as its named permitted difference before
    comparing. CP3's "`--release 2.5.1 --check` writes and checks the existing files,
    byte-unchanged" meets this.
  - `tests/test_resume.py` and `tests/test_cli.py` still build job records and child
    `ManagedRepository`s with `target_workflow_version`/`workflow_version` `"2.3.1"`. CP3's strict
    contract lookup under a job record's release will meet them.
