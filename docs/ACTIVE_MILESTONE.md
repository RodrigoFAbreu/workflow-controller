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
| CP1 -- vendored release trees, one phase list, per-release inventories | complete | `1689540` |
| CP2 -- the Workflow contract module and the two query clients | complete | this checkpoint's commit |
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

### CP2 -- the Workflow contract module and the two query clients (complete)

- **`controller/workflow_contract.py`** (new). It sits after `identity` and before `decision` in
  `controller/__init__.py` and `DEPENDENCY_ORDER`. It imports `errors`, and also `runtime`, for
  the `runtime.write_bytes` private copy that Design B requires. Both come earlier in the order.
  - `WorkflowContract` and `RELEASE_CONTRACTS`: exactly `2.5.1` (`controller_rule`,
    `revision_coherence`, no digests) and `2.6.0` (`workflow_query` for both, with the sha256 of
    `scripts/workflow_state.py` and `scripts/workflow_fingerprint.py` from the Manager manifest).
    `contract_for` refuses an unknown release with `UnsupportedWorkflowVersionError`, reason
    `no_workflow_contract`.
  - `resolve_feedback_path(root, contract, id) -> FeedbackPath` and
    `plan_review_publication_status(root, contract, id) -> PublicationStatus |
    PublicationRefusal`. Both take the contract, because the runner needs its digests. The id is
    passed as one `--<query>=<id>` token, so an id that starts with `-` cannot be read as an
    option.
  - Validation. A feedback answer needs exactly the five keys, all strings, the id echoed, a
    known layout, normalised relative POSIX paths, and the directory and file paths that layout
    implies. A status answer needs the five always-present strings and only the documented
    per-row keys, with their types. The row must be a string from the table and carry its paired
    status, and the id must be echoed. Exit 1 whose stdout is exactly
    `{"error": "PlanReviewBindingInconsistentError", "message": <str>}` is a
    `PublicationRefusal`. Any other exit 1 (a traceback) is an error.
  - `_run_query` reads both scripts once each (regular files only) and checks their digests.
    It writes exactly those bytes into a `TemporaryDirectory(prefix="workflow-controller-query-")`
    through `runtime.write_bytes`, and runs `sys.executable -B -E -s <private>/<script>` with
    `cwd` the target root, stdin closed, a 120 s timeout and bytes output. The directory is
    removed on every path. `_execute_query` is the private runner hook, and it replaces only the
    execution step.
- **`WorkflowQueryError`** (new, `errors.py`, `WORKFLOW_QUERY_FAILED`, exit 20 through the
  existing `ControllerError` mapping). It is the only exception the runner lets out. Its reasons:
  - `query_script_modified`: missing, unreadable, not a regular file, or the wrong digest.
    Nothing is executed;
  - `query_private_copy_failed`: the directory could not be created, written or removed. A
    removal failure discards the answer. After an earlier failure, the removal error is added
    to that failure's evidence under `private_dir_removal_error`;
  - `query_launch_failed`: an `OSError` at launch, or a `ValueError` from an argument
    `subprocess` cannot pass (a NUL byte in the id). The review of this checkpoint's diff found
    that the `ValueError` would have escaped;
  - `query_timeout`, `query_failed` (an undocumented exit status), `query_output_invalid`, and
    `no_workflow_query` (a contract without digests).

  The evidence carries the release, query, work item, `argv`, `cwd`, return code and output
  tails, plus the path and both digests for a modified script.
- **Not wired yet.** No decision, job or evidence code calls the module. The answers seam
  (`WorkflowAnswers`, `QueryAnswers` with its per-call memo, `bind`, `BoundContract`) is left to
  CP3, whose file list names `bind` and `BoundContract`.
- **`tests/test_workflow_contract.py`** (new, 45 tests, about 7 s). Disposable repositories run
  the vendored 2.6.0 tree. They are seeded through Workflow's own writers and its real plan-bundle
  generator. A 2.5.1-created item is seeded under the vendored 2.5.1 tree and then moved to 2.6.0
  by overwriting the tree. The tests cover:
  - the table: digests equal to both `RELEASE.json` records, `VALIDATED_WORKFLOW_RELEASES`
    included in the table, the status set equal to the vendored script's ten
    `PLAN_REVIEW_STATUS_*` values, and the unknown-release refusal;
  - feedback: `scoped` (stamped, directory absent and still absent after the query),
    `legacy-scoped`, `legacy-flat`, an unknown id, an unknown or `null` layout, an undecidable
    state file, and malformed ids including `--help`;
  - status, real rows: 2, 3, 4a, 4b, 4c (no fresh id), 5, 9 (`bundle_verifies` false) and the
    4d refusal. No Workflow writer produces row 4d, so the test moves a published item's phase by
    hand. A `"1"`-governed item and an unknown id are errors. The queries write nothing to the
    target;
  - isolation (I6):
    - either script modified by one appended sentinel-writing line, for both queries: refused,
      the hook is never called, and no sentinel appears. A missing script, or one replaced by a
      directory, is also refused;
    - planted `scripts/uuid.py`/`scripts/secrets.py`, a planted
      `scripts/__pycache__/workflow_fingerprint.*.pyc` with the source's mtime and size, and a
      shadowing `PYTHONPATH`: none runs, both answers are correct, and no `__pycache__` is
      written. Each plant has a positive control proving it runs in place;
    - the private directory holds exactly the vendored bytes of the two scripts when the query
      runs, and is gone afterwards;
  - runner failures: the directory is removed after success, a failing query, an injected
    timeout and a real 1 ms timeout. Each copy failure is injected by patching: creation with
    `ENOSPC`, and the write with `ENOSPC` and with `RuntimeContainmentError`. Removal failures
    are covered on the success and failure paths. Also covered: a launch `OSError` and the
    NUL-byte id. Only `WorkflowQueryError` escapes;
  - validation through the hook: 18 feedback and 21 status violations, each refused with its
    reason, and the valid shapes (row 4c without a fresh id, row 4a with `null`, row 1 with
    five keys, an advisory, a refusal) accepted.

  Deliberately broken runners were caught. Without `-E`, the `PYTHONPATH` test fails. Run in
  place, the shadow, `.pyc` and private-directory tests fail. Without the digest comparison,
  the modified-script test fails.
- **Query wall time** (read, digest check and copy included). The item was bound on a disposable
  repository, Python 3.14.7, 20 runs each. `--resolve-feedback-path` took a median of 43 ms
  (42-43 ms). `--plan-review-publication-status` took a median of 105 ms (104-106 ms). The read
  and digest check alone took 0.5 ms.
- **Verification.**
  - Named modules: `tests.test_workflow_contract`, `test_package_structure`,
    `test_write_containment`, `test_identity`, `test_plan_document_consistency`,
    `test_workflow_releases`, and `test_packaged_runtime` with
    `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`.
  - Full sharded run, `python3 tools/run_tests.py`, in the foreground: 2132 tests in 8 shards,
    PASS, exact coverage, 80.2 s wall.
  - As in CP1, this session is a Controller-launched worker, and it ran the suite under a
    reaping-subreaper wrapper.
- **Notes for later checkpoints.**
  - The seeding helper (`_seed_target` with `_SEED_SCRIPT`, stages `route`/`publish`/`ready`/
    `revise`, `upgrade_to`) is in `tests/test_workflow_contract.py`. CP4 and CP5 may move it into
    `tests/fixtures.py` when they need it.
  - The generator runs a plain `python3`, which writes `scripts/__pycache__` into a target (and
    into the bundle's `files/`) unless `PYTHONDONTWRITEBYTECODE` is set.
