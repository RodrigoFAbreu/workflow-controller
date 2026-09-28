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
| CP2 -- the Workflow contract module and the two query clients | complete | `47b51f2` |
| CP3 -- release-aware feedback resolution | complete | `43a6ecb` |
| CP4 -- plan-review publication status and per-release writer declarations | complete | `53d9601` |
| CP5 -- admission of 2.6.0 and 2.5.1 → 2.6.0 migration | complete | `f931d24` |
| CP6 -- documentation, release 1.3.0 and full verification | complete | this checkpoint's commit |

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

### CP3 -- release-aware feedback resolution (complete)

- **The answers seam** (`controller/workflow_contract.py`). `QueryAnswers` runs the two real
  queries, each at most once per `(root, work item)` for as long as the object lives. A failed
  query is not remembered. `BoundContract(contract, answers)` and `bind(contract)` give a fresh
  `QueryAnswers` for a `workflow_query` contract and `answers=None` for 2.5.1.
  `bind_release(release)` is `bind(contract_for(release))`. The private `_answers_factory` hook,
  which `bind` consults, is set only by the golden generators. No public entry point takes a
  provider, and a test pins `decide`'s, `execute_step`'s and `bind`'s signatures.
- **Resolver** (`controller/evidence.py`). `resolve_feedback_dir(root, id, bound)` returns
  Workflow's `feedback_dir` under a `workflow_query` contract. Under 2.5.1 it runs the old body
  unchanged. A `WorkflowQueryError` propagates and is never answered by the 2.5.1 rule.
  `functional_review_findings_path`, `functional_review_consumed_marker_path` (Workflow's name,
  next to `FUNCTIONAL_REVIEW.md`) and `functional_review_findings_consumed` take the binding too.
  `decide` binds once per call, from `managed_repo.workflow_version`, and passes the binding to
  the nine handler call sites. `bound` is a required argument everywhere, so no caller can fall
  back to the 2.5.1 rule by omission.
- **Before every decision** (`controller/job.py`, `_refuse_changed_release`). The check runs in
  `_execute_step_locked` right after `repository_preflight` returns, for a `Gate` and a `Proceed`
  alike. That is before `_branch_gate_record`, the state re-read, `_capture_pre_state` and
  `decide`.
  - `managed_repo.installed_workflow_version(root)` (new) is a plain read through
    `_read_manifest`. It also maps a stray `OSError` to `MalformedInstallationManifestError`:
    Python 3.12's `Path.is_file` raises one for an unsearchable directory, and 3.12 is the
    supported floor. The self-review found this; without it the error could have escaped
    verification.
  - Any mismatch, or a missing or unreadable manifest, raises `WorkflowReleaseChangedError`
    (new, `WORKFLOW_RELEASE_CHANGED`, exit 20). Its evidence is `admitted`, `installed` (`null`
    plus `manifest_error` `{code, message}` when unreadable), `preflight_action`,
    `preflight_gate` and `preflight_events`.
  - The message says that only the decision was refused. It names a returned gate as not
    recorded and rediscovered next time, an action other than `none` as completed and recorded,
    and a close-out whenever `preflight_events` holds `closed`. When the preflight's own
    outcome is `closed_out`, the close-out is stated once.
  - `milestone_branch.Context.events` (new, default `None`) collects every event `_event`
    writes. `_event` is the only binding-event writer, which was checked.
- **Pre-state capture.** `_capture_pre_state` binds its own contract for
  `functional_review_consumed_blob`. A query failure there is a decision-time refusal, before
  `decide`.
- **Verification** (`_row_clauses_failure`, the one helper `_verify_transition` and
  `_row2_verified` share). It takes `release`, the job record's `target_workflow_version`, and
  runs `_verification_contract` first. That checks the installed release against the record
  and binds once. A mismatch, an unreadable manifest, or a missing or uncontracted recorded
  release (`contract_error`) all give `workflow_release_changed`.
  - The clauses run inside one `try/except WorkflowQueryError`, which gives
    `workflow_query_failed`. The helper now returns a fourth element, `workflow_error`
    (`code`, `message`, full `evidence`).
  - `_transition_not_observed_evidence` gains the optional `workflow_error` key.
    `_row2_verified` returns five elements.
  - `_reconcile_launched` checks the two reasons ahead of its `INTERRUPTED` and
    `UnreconcilableJobError` branches and records `FAILED`. Launch and `_reconcile_completed`
    already record `FAILED` for an unverified job.
  - `PredicateFn`, `PredicateDetailFn` and `PostconditionFn` gain a fourth positional `bound`.
    Every clause takes it. The six feedback readers use it, and the rest ignore it.
- **Goldens** (`--release`, default `2.5.1`).
  - `--release 2.5.1` checks the existing files. They are byte-unchanged, and the derivation of
    both is byte-identical to HEAD's (compared case by case).
  - `--release 2.6.0` writes `plan_stage_decisions.2.6.0.json` (3552 cases, 75 distinct
    decisions, 0.7 s, 496 KB) and `external_implementation_review_decisions.2.6.0.json` (114
    cases, 25 KB). Workflow's answers are replayed through the hook as recorded values, in
    2.6.0's own words (`RecordedAnswers`).
  - Each scenario runs once per layout (`scoped`, `legacy-scoped`, `legacy-flat`), its feedback
    written at that layout's path, with the phase's ordinary status. At the `"2.1"`/`"2.2"`
    plan phases it also runs once per status class on `scoped`: `bound`, `bound_advisory`,
    `content_drifted`, `bundle_unverified`, `legacy_unverified`, `refusal` and `unexpected` at
    the ready phases, and `ordinary`, `refusal` and `unexpected` elsewhere. This reads "also
    runs once per status class" as additional runs rather than a cross product with the
    layouts, which would double the file.
  - A `"1"`-governed item runs every layout with status `-`, and its recorded status query
    fails as the real one does. CP3 consumes no status yet, so the status variants decide
    alike until CP4 regenerates the file.
- **Tests** (32 new).
  - `test_evidence`: real 2.6.0 resolution. A stamped `scoped` item resolves scoped before its
    directory exists, where the 2.5.1 rule says flat. Also covered: the legacy layouts, the
    functional-review helpers, a failure propagating from the resolver and from `decide`, one
    query answering four reads in one decision (and a new query per decision), no query under
    2.5.1, and the signature pins.
  - `test_evidence` golden checks: both 2.6.0 files re-derive byte-equal, the 2.5.1 files are
    the originals, and the coverage is exact. A cross-check derives both releases: wherever
    Workflow answers the layout the 2.5.1 rule would find, with the ordinary status, every
    2.6.0 decision equals the 2.5.1 one.
  - `test_job`: a query failure in the pre-state capture (`decide` never reached), in `decide`
    (the second query fails through the runner hook), and at the CLI (exit 20); no record and no
    worker in each case. The refusal wording for each preflight outcome shape. The gate case
    (`pr_closed_unmerged` not recorded, then rediscovered once restored).
  - `test_job`, the real close-out case: the merge lands a Workflow update and an unbound item,
    and the preflight closes out and binds it. Evidence `preflight_action: bound` and
    `closed` before `bound` in the events; the record is `CLOSED` and nothing is launched.
  - `test_resume` (`VerificationWorkflowFailureTest`): each of the six query-reading clauses on
    real 2.6.0 scripts, broken for real by the worker's `feedback_layout: null`. Each ends
    `FAILED` / `workflow_query_failed` at launch and at `resume` from `COMPLETED` and
    `LAUNCHED`, with nothing pending, no relaunch, and a next step refused with
    `WorkflowQueryError`. Each also has a positive control that verifies `FINISHED`.
  - `test_resume`, the other verification failures: a script replaced by the worker
    (`query_script_modified`, all three paths, never executed); an `ENOSPC` private-copy write
    armed after the worker (launch, `COMPLETED`); a changed release on all three paths (M5); a
    removed, non-JSON and `schema_version: 2` manifest on all three paths, with the next step
    gating once restored; and a record with no or an uncontracted release.
  - `test_cli`: a `run` held by the worker-created pause file while the manifest is changed,
    removed or malformed. Exit 20, one worker and one record, and the refusal's evidence taken
    by a spy. CLI `resume` with the manifest removed or malformed, from `COMPLETED` and
    `LAUNCHED`: exit 20 at `inspect`, the record byte-unchanged and pending, and it reconciles
    `FINISHED` once restored.
  - `test_managed_repo`: `installed_workflow_version`.
- **Existing tests changed** (the plan's named exception, no assertion changed except where
  noted):
  - `fixtures.build_target_managed_repository` now writes the reference manifest when none
    exists, excluded from Git through `.git/info/exclude`. It returns the release the manifest
    declares.
  - `fixtures.reference_binding()` is new. The 2.5.1 binding is passed at the direct clause and
    resolver call sites: 17 in `test_job_validation`, one in `test_job`, three in `test_evidence`.
  - `target_workflow_version`/`workflow_version` `"2.3.1"` became `"2.5.1"` in `test_resume` (a
    record builder and a child Controller) and `test_cli` (a record builder). A real record only
    ever carries an admitted release.
  - `SelfLoopPredicateSingleSiteTest.test_both_sites_evaluate_the_predicate_through_the_helper`
    needed a root with the reference manifest instead of `/nonexistent`, because the release
    check runs before every clause. It also needed the `release` argument, a four-argument fake
    detail, and a fifth `None` in `_row2_verified`'s expected tuple.
- **Mutation checks.** Each of these mutations fails the new tests:
  - dropping the `WorkflowQueryError` catch;
  - dropping `_reconcile_launched`'s Workflow branch;
  - dropping the pre-decision re-check;
  - dropping the verification release comparison;
  - making `resolve_feedback_dir` ignore the contract.
- **Verification.**
  - Goldens: `generate_external_implementation_review_decisions.py --release 2.5.1 --check`,
    `--release 2.6.0 --check` for both generators, and `generate_no_policy_lifecycle.py --check`
    report current.
  - `generate_plan_stage_decisions.py --release 2.5.1 --check` exits 1, as it does at the base
    commit and at CP1: this is the permitted `AMENDING_PLAN` reason difference that
    `tests.test_golden_plan_stage_decisions` reverts. That test passes, the file is
    byte-unchanged, and the derivation equals HEAD's.
  - Named modules: `test_evidence`, `test_job`, `test_resume`, `test_cli`, `test_job_validation`,
    `test_lifecycle_orchestration`, `test_managed_repo`, `test_workflow_contract`,
    `test_package_structure`, `test_write_containment`, `test_golden_plan_stage_decisions`,
    `test_milestone_branch` and `test_trunk_preflight`: 937 tests, OK. `test_packaged_runtime`
    with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`: 9 tests, OK.
  - Full sharded run, `python3 tools/run_tests.py`, in the foreground: 2163 tests in 8 shards,
    PASS, exact coverage, 95.5 s wall. It ran under the reaping-subreaper wrapper, as in CP1
    and CP2. The record-without-a-release test was added after that run, and passes in the
    named-module run.
- **Notes for later checkpoints.**
  - The guides do not describe `WORKFLOW_RELEASE_CHANGED`, `workflow_release_changed` or
    `workflow_query_failed` yet. Design G assigns them to CP6's `troubleshooting.md` entries.
  - CP4 consumes the recorded status classes. `RecordedAnswers`, `recorded_publication_status`
    and `case_runs` are in `tests/golden/generate_plan_stage_decisions.py`.
    `_postcondition_plan_bundle_coherent` already receives the binding and still ignores it.
  - `tests/test_job.py`'s `_ContractTargetCase` (a `Lifecycle` target on a vendored release,
    driven through `execute_step`/`resume` directly) is reusable for CP4's postcondition query
    failures and CP5's migration scenarios.

### CP4 -- plan-review publication status and per-release writer declarations (complete)

- **The status query at every plan phase** (`controller/evidence.py`). Under a `workflow_query`
  contract, for a `"2.1"`/`"2.2"` item, `decide` reads `--plan-review-publication-status` at the
  three ready phases and the three non-ready ones (`_plan_review_publication`), after the
  REJECTED-marker gate and in place of `plan_bundle_coherence`. `"1"` items and every 2.5.1
  target keep `plan_bundle_coherence` exactly as before. The Controller reads only the answer's
  `status`, `row`, `phase`, `remedy`, `detail`, `advisory` and `fresh_review_content_id`, never
  `plan_review_binding` (I5). One outcome per cell:
  - S1 (rows 2, 3): the phase's own handler, with `plan-review publication status: row N (BOUND)`
    and any advisory appended to its evidence (`_with_evidence`, built field by field);
  - S2 (rows 4a-4c): the stale-plan-bundle gate (`_stale_plan_bundle_gate` with the answer). The
    evidence names the row and status, `fresh_review_content_id` only when non-null, and
    Workflow's detail. `what_is_required` quotes Workflow's remedy and detail verbatim and adds
    that the withdrawal discards both review stages. Rows 4b/4c: the author-file steps, then the
    generator. Row 4a: the no-restore diagnostic gate (`_content_drifted_gate`), a function of
    the answer and the item id alone, making every statement Design D lists. At
    `AWAITING_PLAN_APPROVAL` it replaces the approval gate;
  - S3/N2 (a refusal, rows 4d/6): `plan_review_binding_inconsistent`, quoting Workflow's message;
  - S4/N4 (any other status, or an answer for another phase): `unexpected_plan_review_status`;
  - S5/N3: the `WorkflowQueryError` propagates (exit 20);
  - N1 (rows 5, 7-11): dispatch as before, with the row and status in the evidence.

  `HumanGate` has no id, so each new gate's `reason` names it. Every gate this checkpoint adds
  with no automatic continuation renders `workflow-controller --work-item <id> explain
  <repository>` (`_explain_gate_command`, `shlex`-quoted), never Workflow's withdrawal. The older
  `_explain_command` form is unchanged (Decision 12).
- **The author-input directory** (`plan_author_inputs_dir`). Under a `workflow_query` contract the
  plan-bundle recovery steps name `.ai-review/<id>/plan-inputs/` when it exists, else `current/`
  (the directory the 2.6.0 generator reads); under 2.5.1 always `current/`, byte-identical.
- **The REJECTED-marker gate**: under the query contract, for a `"2.1"`/`"2.2"` item, its
  plan-stage branch covers all three ready phases, `AWAITING_PLAN_APPROVAL` included. 2.5.1 and
  `"1"` items keep the bare generator there.
- **The plan-stage postcondition** (`controller/job.py`). For a `"2.1"`/`"2.2"` item under the
  query contract, `_postcondition_plan_bundle_coherent` is `evidence.plan_review_bound`: the item
  at `AWAITING_LOCAL_PLAN_REVIEW` and Workflow's status `BOUND` there. A query failure is caught
  by `_row_clauses_failure` (`workflow_query_failed`).
- **Per-release writer declarations.** `job.expected_outcomes_for(release)` returns
  `EXPECTED_OUTCOMES` for 2.5.1 and, for 2.6.0, substitutes `bind_plan_review_bundle`
  (`milestone-plan.md:445`, `apply-plan-review.md:294`) for the PLANNING `"2.1"`/`"2.2"`,
  `NO_PHASE` and both REVISING_PLAN rows. The PLANNING `"1"` row keeps `publish_plan_revision`,
  with `verify_plan_review_bundle`, `state_transaction` and `bind_plan_review_bundle` allowlisted
  as `trailing_calls`. Only `writer_calls` differ; each row keeps its branch (`None`), so
  verification, which reads `EXPECTED_OUTCOMES`, is release-independent. Property 5 is `[]` for
  both releases. The unsubstituted table gives exactly the 14 measured violations on 2.6.0.
- **Comments corrected**: `current_bundle_id` is written by 2.6.0's bind, and still not read.
- **The 2.6.0 plan-stage golden was regenerated once, deliberately.** CP3 generated it before any
  decision read the status, so every status variant decided alike. Now each status class pins
  its own outcome: 3552 cases, 136 distinct decisions (75 before). Every other golden is
  byte-unchanged, and the 2.5.1 derivations of both decision goldens equal HEAD's (compared in a
  scratch worktree of `43a6ecb`).
- **Fixtures.** CP2's seeding helper moved to `tests/fixtures.py` as `seed_workflow_item`
  (`WORKFLOW_SEED_SCRIPT`), with a new `generate` stage (generated, not bound: row 9, "bind
  only") and `extra_protected_paths`. The seeded `CONTEXT_FILES.txt` now names `README.md`: an
  empty one is the generator's stub, which the recovery steps rightly treat as absent.
  `run_workflow_python` runs code against a target's own scripts.
- **Tests.**
  - `test_job_validation`: property 5 per release through `expected_outcomes_for`; 2.6.0
    explicitly before admission; the 14 measured violations; 2.5.1 is the table itself; only the
    six measured rows' writer calls differ, with equal branches; the `"1"` row's allowlist
    (dropping an entry reports it again); an undeclared release refused. The query-contract
    postcondition: BOUND satisfied with no bundle on disk, every other answer not, a query
    failure propagating, row 7's new key, a `"1"` item on revision coherence.
  - `test_decision` (I4): no automatic triple, no phase-table selection and no decision in a
    decide sweep of both plan-stage goldens (over 1000 ready-phase cases, every status class)
    dispatches or advertises `/milestone-plan` at a ready phase. The ready and non-ready sets
    equal the vendored 2.6.0 script's.
  - `test_evidence`, real 2.6.0 bundles and scripts:
    - S1: row 2 at each ready phase (through the real local and manual APPROVE writers); row 3.
    - S2 row 4b: `plan-inputs/` with and without author files, and `current/` with and without,
      each carried out with the real generator back to `BOUND`.
    - S2 row 4c: a damaged legacy bundle (no fresh id in the evidence), a completed 2.5.1
      withdrawal (step 0 restores from the quarantine), and `plan-inputs/` with and without
      author files, each carried out back to row 3.
    - S2 at `AWAITING_PLAN_APPROVAL`, replacing the approval gate.
    - S3 row 4d at each ready phase.
    - N1: row 9 at PLANNING, rows 5, 8, 10 and 11 at REVISING_PLAN, and row 5 at AMENDING_PLAN
      (declined as before). Row 7: the real query answers row 7 for a routed item with no
      registry, but `target_state.read` refuses that state before any decision (a declared
      registry must exist; pre-existing and release-independent), so that cell is driven with the
      item's view directly.
    - N2: row 6 at each non-ready phase.
  - `test_evidence`, row 4a on a real bound bundle in all eight listed cases: plan bytes, a
    declarations exclusion, the plan's execute bit (`core.fileMode` true), the fourth protected
    path deleted (no fresh id in the evidence) or replaced by a link, and the plan edited with its
    `current/files/` copy deleted, re-written or mode-flipped. Each gate's command parses with
    `cli.build_parser()`, runs through `cli.main`, exits 0, prints the same gate, and leaves an
    identical `lstat` snapshot of the target, `.ai-review/<id>/` and every outside link target.
  - Row 4a, parent directory linked after the gate is rendered. Measured: Workflow's query fails
    on the link (`UnclassifiedPathError: docs/design`), so `explain` exits 20. The snapshot is
    identical.
  - S5 real: the plan document deleted, or replaced by a link. The query exits 1, and CLI `step`
    exits 20 with no job record, no worker and an identical snapshot.
  - Through the answers seam: all fifteen ready and twelve non-ready cells. This includes S4/N4
    phase mismatches and S5/N3 through the private runner hook (a timeout). The row-4a gate is
    byte-identical with `current/` present or removed. `"1"` items and 2.5.1 never query. The
    REJECTED gate's plan-stage branch applies at all three ready phases, naming `plan-inputs/`
    when present. 2.5.1 and `"1"` items keep the bare generator at `AWAITING_PLAN_APPROVAL`.
  - `test_evidence`'s CP3 cross-check is restated. With the ordinary status, every 2.6.0 decision
    is the 2.5.1 one plus the row in its evidence. There are two named, non-vacuous exceptions: a
    2.5.1 stale-bundle gate (revision coherence) becomes the handler's decision under `BOUND`, and
    a REJECTED marker at `AWAITING_PLAN_APPROVAL` takes the plan-stage branch.
  - `test_resume`: on a real row-9 item whose worker writes exactly the state Workflow's own bind
    writes, the postcondition verifies `FINISHED`. With the bundle then damaged, it fails as
    `postcondition_not_satisfied` (row 4b). With the plan document then deleted, it fails as
    `workflow_query_failed` at launch and at `resume` from `COMPLETED` and `LAUNCHED`. Nothing is
    pending, nothing is relaunched, and the next step refuses. A worker that leaves the item at
    `PLANNING` fails as `phase_not_in_to_any_of`, and `run` stops after one worker; `explain`
    then shows row 9 and the explicit-id command.
  - Existing tests changed: CP3's row-3 plan clause case now needs a `BOUND` status to launch
    `/review-plan`. The harness's synthetic plan bundle never verifies under 2.6.0, so for that
    clause only, the status query is answered as row 2 through the private runner hook. Its
    feedback queries still run the real script. `test_workflow_contract` uses the moved fixture.
- **Mutation checks.** Each of these fails the new tests: S2 rows treated as S1; the author-input
  rule forced to `current/`; the REJECTED branch not extended; the row-4a command using the old
  `explain` form; the phase-mismatch check dropped; the postcondition ignoring the query; a 2.6.0
  bind declaration removed.
- **Verification.**
  - Goldens: `generate_plan_stage_decisions.py --release 2.6.0 --check`, both
    `generate_external_implementation_review_decisions.py` releases and
    `generate_no_policy_lifecycle.py --check` report current. `--release 2.5.1` of the plan-stage
    generator exits 1 for the permitted `AMENDING_PLAN` difference, as at the base commit;
    `tests.test_golden_plan_stage_decisions` passes and the derivation equals HEAD's.
  - Named modules: `test_evidence`, `test_job`, `test_job_validation`, `test_decision`,
    `test_golden_plan_stage_decisions`, `test_workflow_contract`, `test_package_structure`,
    `test_write_containment`, `test_lifecycle_orchestration`, `test_cli` (799 tests, OK);
    `test_resume`, `test_managed_repo`, `test_workflow_releases` (172 tests, OK);
    `test_packaged_runtime` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1` (9 tests, OK).
  - Full sharded run, `python3 tools/run_tests.py`, in the foreground: 2209 tests in 8 shards,
    PASS, exact coverage, 101.9 s wall, under the reaping-subreaper wrapper as in CP1-CP3.
- **Notes for later checkpoints.**
  - CP5's M1b/M1d: `_admit_2_6_0` in `tests/test_evidence.py` patches admission until CP5 admits
    2.6.0, and the `carry_out` helper follows plan-bundle recovery steps literally with the real
    generator.
  - CP6's `troubleshooting.md`: `target_state.read` refuses a routed item whose declared registry
    is not written yet (row 7's state) before any decision, for both releases.

### CP5 -- admission of 2.6.0 and 2.5.1 → 2.6.0 migration (complete)

- **Admission** (`controller/managed_repo.py`). `SUPPORTED_WORKFLOW_LINE = "2.5"` became
  `SUPPORTED_WORKFLOW_LINES = frozenset({"2.5", "2.6"})`, and `VALIDATED_WORKFLOW_RELEASES` is
  `{"2.5.1", "2.6.0"}`. Its comment states what was measured for 2.6.0 and the steps for admitting
  a later release. `REFERENCE_WORKFLOW_RELEASE` stays `"2.5.1"`.
  - Both refusals carry `supported_workflow_lines`, a sorted list, in place of
    `supported_workflow_line` (Decision 5).
  - The wrong-line message names the supported lines and the validated releases, where it named
    the reference release. The unvalidated message names the line the release is in.
  - `errors.UnsupportedWorkflowVersionError`'s docstring says the same.
- **E1-E5** (`controller/milestone_branch.py`, `controller/gitrepo.py`). The `integration_required`
  gate now reads "Workflow 2.5.1 and 2.6.0 have no transition that moves a work item's base, so
  the Controller does not integrate". The `--abandon` problem text names both releases, and so
  does the `MILESTONE_COMPLETE` comment. `merge_trunk`'s docstring gives the reason it stays
  unwired: the answers to E1-E5.
- **Per-release inventories and goldens.** The inventories already iterated
  `VALIDATED_WORKFLOW_RELEASES`, so they now run for 2.6.0 too: the phase and terminal sets, the
  command partition, the user-only set, property 5 and the provenance-recovery phases.
  `test_evidence.WorkflowQueryGoldenTest` now iterates the admitted releases:
  - each admitted release has its own golden in both generators, exactly;
  - each golden re-derives byte-equal. The 2.5.1 plan-stage golden keeps its one named permitted
    difference, and the test asserts that difference is non-vacuous.
- **The pre-admission scaffolding is gone.** `test_evidence._admit_2_6_0` and `test_resume`'s
  inline patch of the old constant are removed. The comments that said 2.6.0 is admitted only at
  CP5 are corrected in `test_job` and `test_job_validation`.
- **Fixtures.**
  - `run_workflow_seed` (new) runs `WORKFLOW_SEED_SCRIPT` in an existing target. It takes a
    `checkpoint_ids` option. `seed_workflow_item` calls it, and its seeds are byte-identical.
  - `carry_out_plan_recovery_steps` (new) is CP4's `carry_out`, moved from `test_evidence` and
    taking the work item as an argument. `test_evidence` delegates to it.
- **`tests/test_workflow_release_migration.py`** (new, 10 tests, about 17 s).
  - The targets are clones of a bare origin, with the vendored 2.5.1 tree, the Workflow
    configuration and, except for M4 and M5, the policy. They are driven through `cli.main` with
    the fake `gh` and the scripted worker, on `test_trunk_orchestration_e2e`'s harness.
  - Items are seeded by the real writers and generator of the installed release.
  - A worker's Workflow write is the state the real writer produces, computed by the test and
    then restored (`real_write`).
  - The milestone is bound through the Controller's own `repository_preflight`. The operator
    commits the narrative (`docs/ACTIVE_MILESTONE.md`, excluded from the plan-stage content), and
    the next preflight pushes the branch and opens the Draft PR.
  - The update step replaces the twenty vendored files, rewrites `installation.json` and commits
    exactly those paths.
  - Scenarios:
    - **M1** (legacy-flat, and legacy-scoped with `.ai-review/<id>/feedback/` present).
      - `inspect` admits 2.6.0, and the feedback query answers the 2.5.1 rule's directory. The
        status is row 3, and the decision is the pre-update `/review-plan`.
      - The update leaves the binding record byte-unchanged.
      - The dispatched worker writes the real 2.6.0 `REVISE`, and the job verifies `FINISHED`
        under 2.6.0. The same step pushed the update commit, and the PR is still #1.
      - After the `REVISE`: row 10 with a non-legacy `CONSUMED` record, then
        `/apply-plan-review`.
    - **M1a**: row 5 after a 2.5.1 `REVISE`.
      - The worker writes the real `ensure_plan_review_binding_marker` state, which gives row 11
        with a legacy record.
      - `run` stops after one failed job (`phase_not_in_to_any_of`).
      - The next step dispatches `/apply-plan-review` again, with row 11 in its evidence.
    - **M1b**: the real 2.5.1 `withdraw_bundle`, then the marker planted with the text it holds
      between the quarantine rename and its own removal.
      - The REJECTED gate fires, and the status query never runs (a spy on the private runner
        hook). The steps name `current/`, with step 0 restoring from the quarantine.
      - Once the steps are carried out with the real 2.6.0 generator, the marker is gone, the
        status is row 3 and the decision is `/review-plan`.
    - **M1c**: a completed withdrawal gives row 4c and the stale-plan-bundle gate.
      - The gate quotes Workflow's remedy and detail, and its step 0 names the quarantine.
      - On a copy, Workflow's bare remedy fails with `MissingReviewContentIdStatementError`.
      - The gate's steps give row 3.
    - **M1d**: as M1b at `AWAITING_PLAN_APPROVAL`, after the real 2.5.1 local and manual
      `APPROVE`.
      - The gate takes the plan-stage branch, never the bare generator, which fails on a copy.
      - The steps give row 3. The real 2.6.0 `assert_plan_review_bundle_bound` accepts the item,
        and the decision is the `/approve-review plan` gate with row 3 in its evidence.
    - **M2**: `IMPLEMENTING` after the plan approval and CP1, all through the real 2.5.1 writers.
      - The writers used: `build_approval_record` and `apply_plan_approval`, the approval commit
        with its trailers, then identity, claim, `IN_PROGRESS`, `complete_checkpoint`, the
        checkpoint commit and the release.
      - The PR opens from the approval commit.
      - After the update, `/milestone-implement` is decided with the same evidence, and the
        feedback resolves legacy-flat.
      - The real 2.6.0 `claim_checkpoint` of CP2 succeeds. It creates the new
        `*.lifecycle.lock` under `<git-common-dir>/ai-workflow/checkpoint-claims/`, which 2.5.1
        never wrote.
    - **M3**: the trunk is updated by a second clone while the milestone runs, using
      `test_trunk_orchestration_e2e`'s scripted lifecycle.
      - On the branch no query runs, and every job records `target_workflow_version: 2.5.1`.
      - Readiness gives `integration_required` with the new text.
      - After the manual merge, `execute_step` closes out and then refuses
        (`WORKFLOW_RELEASE_CHANGED`). The evidence is `admitted` 2.5.1, `installed` 2.6.0,
        `preflight_action: closed_out` and `closed` in the events. The message says the
        close-out completed and was recorded.
      - The record is `CLOSED`, HEAD is `main` at the merge, and no job and no worker were added.
      - The next `inspect` reports 2.6.0, and `explain` exits 0.
    - **M4**: after the update, the real 2.6.0 `route_work_item` stamps the item `scoped`.
      - Before its directory exists, the query and `resolve_feedback_dir` say
        `.ai-review/wi-1/feedback`, where the 2.5.1 rule says flat.
      - A `BLOCK` verdict at the flat path does not change the decision.
      - At the scoped path it gives the `BLOCK` gate.
    - **M5**: a `/review-plan` worker records the real 2.5.1 `REVISE` and then overlays the
      2.6.0 files and manifest, uncommitted (`git restore --source`). This is the Manager's
      write, not its commit.
      - At launch the job is `FAILED` with `workflow_release_changed` (`recorded` 2.5.1,
        `installed` 2.6.0), and nothing is pending.
      - Rewritten as `COMPLETED` and then as `LAUNCHED`, CLI `resume` admits 2.6.0 at `inspect`
        and reconciles the same `FAILED`, exit 0, with no new worker.
      - `explain` then decides under 2.6.0: row 5, `/apply-plan-review`.
- **The local-only variant**, `test_integration_disposable_repo.RealManagerMigrationTest`. It is
  skipped without `workflow-manager`, and it ran here.
  - It runs a real `workflow-manager --release-version 2.5.1 bootstrap` and seeds the item with
    the real 2.5.1 writers.
  - It then runs a real `--release-version 2.6.0 update` and `verify`. The twenty paths the CI
    simulation writes hold exactly the vendored 2.6.0 bytes after the real update.
  - The rest is M1's assertions with the real Manager's `inspect`: legacy-flat, row 3,
    `/review-plan` with row 3 in the evidence; then a real 2.6.0 `REVISE`, row 10 and
    `/apply-plan-review`.
- **Other new tests.**
  - `test_managed_repo`:
    - the sets and the reference release are pinned, and `SUPPORTED_WORKFLOW_LINE` is gone;
    - `test_2_6_0_refuses_outside_supported_line` is inverted to `test_2_6_0_is_admitted`;
    - 2.6.1 is refused `unvalidated_release`, and 2.7.0 `outside_supported_line`, each with the
      list key;
    - the 2.4.0 and 2.5.0 cases now expect the list and the two releases.
  - `test_cli.InspectCommandTest`:
    - a 2.6.0 target in text and JSON (`_build_managed_target` gains `workflow_version`);
    - 2.6.1 and 2.7.0 refused through `cli.main`, exit 20, each message naming its own case.
  - `test_gitrepo.MergeTrunkTest`: no Controller module other than `gitrepo` names `merge_trunk`.
- **Mutation checks.** Each of these fails the new tests:
  - `VALIDATED_WORKFLOW_RELEASES` back to `{"2.5.1"}`: `test_managed_repo`, M1 and M5;
  - `SUPPORTED_WORKFLOW_LINES` back to `{"2.5"}`: `test_managed_repo` and the CLI cases.
- **Verification.**
  - `python3 tools/workflow_releases.py check`: exit 0.
  - Goldens:
    - both generators with `--release 2.6.0 --check` report current;
    - the external-implementation-review generator with `--release 2.5.1 --check` reports
      current;
    - `generate_no_policy_lifecycle.py --check` reports current;
    - the plan-stage generator with `--release 2.5.1 --check` exits 1, as at the base commit, for
      the permitted `AMENDING_PLAN` difference. `tests.test_golden_plan_stage_decisions` and the
      per-release golden test pass.
  - Named modules: `test_workflow_release_migration`, `test_managed_repo`, `test_cli`,
    `test_evidence`, `test_gitrepo`, `test_milestone_branch`, `test_trunk_orchestration_e2e`,
    `test_workflow_releases`, `test_workflow_contract`, `test_golden_plan_stage_decisions`,
    `test_decision`, `test_target_state`, `test_job_validation`, `test_package_structure` and
    `test_write_containment`: 864 tests, OK.
  - `test_integration_disposable_repo.RealManagerMigrationTest` with the real `workflow-manager`
    on `PATH`: OK.
  - `test_packaged_runtime` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`: 9 tests, OK.
  - Full sharded run, `python3 tools/run_tests.py`, in the foreground: 2227 tests in 8 shards,
    PASS, exact coverage, 98.6 s wall. As in CP1-CP4, it ran under the reaping-subreaper wrapper.
- **Notes for CP6.** Guide text that still describes 2.5.1 alone, which Design G assigns to CP6:
  - `milestone-branches.md`'s `integration_required` and abandon passages (lines 32, 84, 132);
  - `ci-and-releases.md` and `automation.md`;
  - `troubleshooting.md`'s admitted-release refusal. The refusal key is now
    `supported_workflow_lines`, and the wrong-line message names the validated releases.

### CP6 -- documentation, release 1.3.0 and full verification (complete)

- **Guides** (`docs/guide/`).
  - `installation.md`: "Supported Workflow releases" (the admitted set per Controller release, the
    two refusal cases, a pointer to ADR 0006), and "Moving a target to another Workflow release".
    That section covers the Manager's `update`, which commits and migrates nothing, and the
    between-milestones procedure. In flight it names only the phases M1-M1d and M2 prove
    (`AWAITING_LOCAL_PLAN_REVIEW`, `REVISING_PLAN`, `AWAITING_PLAN_APPROVAL`, and `IMPLEMENTING`
    between checkpoints). It rules out a plan-approval journal in flight and an update while a
    worker runs (M5). It also covers re-planning at `IMPLEMENTING`, the trunk-first case (M3),
    the 2.6.0 lag probe on linked worktrees, and the rollback refusal.
  - `concepts.md`: the glossary's "Target" names the two releases, and a new "Workflow release"
    entry.
  - `automation.md`: a safety-model bullet for the release re-check and the queries. The
    automatic table is stated for both releases, with the 2.6.0 bind step, the row-9 recovery
    and I4 under it. A new section, "Workflow's queries (2.6.0 and later)", covers the two
    queries, how they run, their fail-closed rule and the plan-stage outcome table (S1-S5 and
    N1-N4 by phase class), including `plan_review_binding_inconsistent` and
    `unexpected_plan_review_status`.
  - `troubleshooting.md`: exit `20`'s row names the two new refusals. A new section, "Workflow
    releases and Workflow's queries", holds five entries:
    - the admitted-release refusal, moved there, with both `reason`s and the key rename;
    - `WORKFLOW_RELEASE_CHANGED` before a decision (`installed: null` and `manifest_error`, and
      what the preflight did: action, gate, `closed`), and `workflow_release_changed` at
      verification, including CLI `resume` refusing at `inspect`;
    - `WORKFLOW_QUERY_FAILED` and `workflow_query_failed`, with a table of every `reason`;
    - the stale-plan-bundle gate under 2.6.0: rows 4b/4c follow the author-file steps in
      `plan-inputs/` or `current/`, then the generator. Row 4a offers no restore; the entry
      gives its `explain` command, why no restore is offered, what the bound state covers, and
      the overwrite warning. It also says the withdrawal is never the `safe_resume_command`,
      and that the REJECTED-marker gate gives the same steps at all three ready phases;
    - the two new plan-stage gates.

    CP4's note is also an entry: a routed item whose registry is not written yet is refused as
    `MalformedTargetRegistryError` under every release.
  - `milestone-branches.md`: the bind, `integration_required` and `--abandon` passages name
    2.5.1 and 2.6.0. The `integration_required` paragraph points to ADR 0006's named follow-up.
  - `ci-and-releases.md`: the release-commit and ruleset passages name both releases.
  - `development.md`: a new "Workflow release trees" section. It covers the vendored trees,
    `tools/workflow_releases.py` `check`/`sync`, the Manager-manifest comparison, the per-release
    golden generators with `--check`, the 2.5.1 plan-stage `--check`'s known difference, and the
    five steps for admitting a future release.
- **ADRs.** `docs/adr/0006-workflow-release-admission-and-per-release-contracts.md` (new). It
  records admission by exact release, the contract table, the release re-check and per-job
  pinning, and the queries as the single authority with I6's execution rule. It also records
  the plan-stage outcome classes, the vendored trees and per-release suites, the E1-E5 table
  with the five-part Workflow follow-up, target updates between milestones (and why this
  repository updates after 1.3.0), the rejected alternatives and the consequences. ADR 0001's
  Context and ADR 0003's drift bullet each gain the one-line pointer. `docs/README.md`'s ADR
  table gains 0006.
- **Release notes.** `docs/releases/1.3.0.md` (new). It covers 2.6.0 admitted, the two queries,
  I6, the plan-stage bind and row 9, the re-check, both new error codes and verification
  reasons, the new gates, the refusal-evidence key rename, the gate text naming both releases,
  2.5.1 otherwise unchanged, and the two 1.2.1 behaviour changes (`source` follows the current
  basis; the 10800 s drain detach bound). `docs/README.md` gains a "Releases" section whose table
  has the 1.3.0 row.
- **Version.** `pyproject.toml` `version = "1.3.0"`.
- **`docs/ROADMAP.md`** is not edited (plan Design G: `/accept-milestone` marks 1.6 complete).
- **Review of the documentation.** Every statement was checked against the code: the gate
  builders in `controller/evidence.py`, `_refuse_changed_release` and `_verification_contract` in
  `controller/job.py` (the evidence keys are `admitted`/`installed` before a decision, and
  `recorded`/`installed` at verification), `_query_failed` in `controller/workflow_contract.py`,
  and the admission messages in `controller/managed_repo.py`. Every relative link and anchor in
  the changed documents resolves, by GitHub's heading-slug rule, and
  `tests.test_plan_document_consistency` passes (every guide invocation line parses under the
  live parser).
- **Verification.**
  - `python3 tools/ci_workflows.py --check`: exit 0.
  - `python3 tools/workflow_releases.py check`: exit 0.
  - Goldens, `--check`: the plan-stage generator with `--release 2.6.0`, the
    external-implementation-review generator with `--release 2.5.1` and `--release 2.6.0`, and
    `generate_no_policy_lifecycle.py` report current (exit 0). The plan-stage generator with
    `--release 2.5.1` exits 1 for the permitted `AMENDING_PLAN` difference, as at the base
    commit; `tests.test_golden_plan_stage_decisions` and the per-release golden test pass in the
    runs below.
  - Full sharded run in this worktree with the bump uncommitted: 2225 of 2227 passed. The two
    failures, `test_release_tools.VersionCommandTest` and
    `test_packaged_runtime.VersionEqualsArtifactTest`, compare `tools/release.py version`, which
    reads the version committed at `HEAD`, with the working tree's `pyproject.toml`. So the
    checkpoint's content was verified committed, in a scratch clone of `f931d24` with exactly
    this worktree's changes applied and committed (content checked byte-identical):
    - `python3 tools/run_tests.py`, in the foreground: 2227 tests in 8 shards, PASS, exact
      coverage, 104.8 s wall;
    - `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime`:
      9 tests, OK.
  - `tools/release.py classify`: `RELEASE_DUE`, "1.3.0 has no tag and no release"
    (`tag=v1.3.0`), exit 0. `classify` takes only a commit on `origin/main`, so it ran in a
    clone of a scratch bare origin that carries this repository's tags (`v1.1.0` to `v1.2.1`).
    Its `main` is a merge commit of `2296ad6` (the current `origin/main`) and the scratch CP6
    commit, made with `git commit-tree`, as GitHub's "Create a merge commit" would. The forge
    side read this repository's real GitHub releases, read-only.
  - **The self-update dry run** (Decision 2 evidence, nothing committed), in that scratch clone:
    - `workflow-manager --release-version 2.6.0 update <clone>`: exit 0. It changed exactly 30
      paths: the 13 command files, six `docs/ai-workflow/` documents and two of its dry-run
      scripts, the three scripts and five conformance suites under `scripts/`, and
      `installation.json`
      (`workflow_version: "2.6.0"`). `workflow-manager verify <clone>`: "installation matches
      workflow 2.6.0", exit 0;
    - `python3 tools/run_tests.py` in the clone, with `workflow-manager` on `PATH`: 2227 tests in
      8 shards, PASS, exact coverage, 188.5 s wall. The 2.6.0 acceptance-matrix suite alone took
      179.3 s, against 80.5 s for 2.5.1's.
      `test_managed_repo.CleanManagedRepositoryTest.test_real_workflow_manager_admits_this_repository`
      and `test_integration_disposable_repo.RealManagerMigrationTest` ran and passed;
    - `python3 -m controller inspect <clone>` from the clone: exit 0, "Workflow 2.6.0, profile
      full".

    The post-release update is therefore a Manager-output-only change.
  - Every full run above ran under the reaping-subreaper wrapper, as in CP1-CP5.

## Implementation review round 1 -- the query's Git (manual external review `REVISE`)

The local review approved round 1, and the manual external review returned `REVISE` with one
blocking finding. A target could make Git run arbitrary commands during a 2.6.0 query. The
reviewer's case was a `.git/info/attributes` clean filter, defined in `.git/config`, which ran
inside the status query's `git hash-object` on a target the Manager verified. That broke I6
and acceptance criteria 7 and 8.

- **Reproduced**, and a second path found. The query's `git diff --name-only <base>` refreshes
  a stale index. That rewrites `.git/index` and fires the target's `post-index-change` hook.
  In Git 2.55 a configured `hook.<name>` hook fires as well, and `core.hooksPath` does not
  stop it.
- **Audit of the 2.6.0 scripts** (static reachability from both entry points, and a traced run
  of every row the tests reach):
  - the queries run only `rev-parse`, `ls-files`, `diff --name-only`, `config --type=bool
    core.fileMode` and `hash-object`;
  - they never write, never produce a patch, a log, a checkout or a fetch, and start no
    non-Git program;
  - every environment they build inherits the caller's.
- **Fix** (`e866ad8`). `workflow_contract._git_isolation` prepares the query's Git: a private
  index copy, no transport, and command-scope overrides for hooks, fsmonitor, split-index
  writes, signature checks, every filter driver and every configured hook. Each override is
  read back and must be in force. The new reason `query_git_not_isolated` refuses a
  target-configured hook command, a populated submodule, an override Git does not apply, an
  irregular index, or a failing preparation command. One timeout covers the whole query. The
  scripts now sit in `<private dir>/scripts/`.
- **Tests.**
  - `GitIsolationTest` (10 tests, each plant shown live in place first).
  - The writes-nothing test now snapshots `.git` too.
  - The reviewer's reproduction on a real Manager-verified 2.6.0 target, in
    `RealManagerMigrationTest`.
  - Every new test fails with the isolation replaced by the plain environment.
- **Docs.** Automation, troubleshooting (the new reason, and the fact that a query run by hand
  in place is not isolated), development (admitting a later release re-checks its queries' Git
  commands), ADR 0006 and the 1.3.0 release notes.
- **Verification.**
  - `python3 tools/run_tests.py`: PASS, 2238 tests in 7 shards, exact coverage, 106.4 s,
    under the reaping-subreaper wrapper;
  - `tools/workflow_releases.py check`: exit 0;
  - `test_packaged_runtime` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`: 9 tests, OK.
- **Outside this finding.** The Controller's own Git commands elsewhere run under the target's
  Git configuration, as they did before this milestone. One example is
  `evidence.functional_review_findings_consumed`'s `git hash-object`. I6 covers the queries only.

## Implementation review round 2 -- a filter that changes content (manual external review `REVISE`)

The local review approved round 2. The manual external review returned `REVISE` with one
blocking finding. Round 1 switched every filter driver off, and that changed what `git
hash-object` answers, which is Workflow's content identity. The reviewer's case was a
Manager-verified 2.6.0 target with `filter.evil.clean = tr a-z A-Z` selected through
`.git/info/attributes`. Released Workflow reports row 4a, or 4c after a 2.5.1 migration. The
Controller reported rows 2/3 `BOUND` and let the phase handler act.

- **Reproduced** on a seeded 2.6.0 target: Workflow in place answered 4a, and the Controller
  answered row 2 `BOUND`.
- **Fix** (`a519a46`). A driver whose `clean` or `process` in force is not empty is also made
  `required` in command scope. Git then fails wherever it would run the program, and so the
  query fails (`query_failed`; the evidence's `refused_filters` names the drivers). It fails
  exactly when Workflow's answer would depend on the program. A driver no hashed path selects,
  and a smudge-only driver, leave the answer at Workflow's own. Before relying on this, the
  2.6.0 query path was checked to let a failing Git command fail it: its only broad `except`
  wraps the bundle-id recomputation, which runs no Git.
- **Rejected alternative.** A check of attributes before the query cannot list every path
  `git hash-object` may be given (ignored files, files in an untracked nested repository). It
  would also refuse every repository that uses Git LFS.
- **Audit of the other facilities**, for the property the finding names: isolation must never
  change an answer.
  - Hooks, split-index writes and signature checks feed nothing the queries read.
  - Without fsmonitor, Git stat-checks every path. It can only find more changed paths, which
    the classification gate refuses, never fewer.
  - No transport only turns a lazy fetch into a failure.
  - The private index copy did change an answer. Its fresh mtime hid a racily clean edit from
    `git diff`, which Git in place lists. Fixed in `816ffc1`: the copy keeps the index's mtime.
- **Tests.**
  - `GitIsolationTest` (13 tests): the reviewer's uppercasing filter, which Workflow in place
    answers 4a and the Controller refuses. The round-1 pass-through variants now refuse as well.
    Filters that no hashed path selects answer as before, and only a driver with a program is
    made required. A racily clean edit is seen as Git in place sees it.
  - The real-Manager reproduction runs a pass-through and an uppercasing filter: Workflow in
    place answers 3 and 4c, and the Controller's query and decision refuse.
  - Each new test fails with its fix removed.
- **Docs.** Automation, troubleshooting (a filter under `query_failed`, with the remedy),
  development (admitting a release re-checks that a failing Git command fails the query), ADR
  0006 (isolation never changes an answer) and the 1.3.0 release notes.
- **Verification.**
  - `python3 tools/run_tests.py`: PASS, 2241 tests in 8 shards, exact coverage, 90.4 s, under
    the reaping-subreaper wrapper;
  - `tools/workflow_releases.py check` and `tools/ci_workflows.py --check`: exit 0;
  - `test_packaged_runtime` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`: 9 tests, OK;
  - goldens `--check`: current, except the known 2.5.1 plan-stage `AMENDING_PLAN` difference.

## Implementation review round 3 -- fsmonitor, hooks, and the filter refusal's reason (manual external review `REVISE`)

The local review approved round 3 with five optional findings. The manual external review
returned `REVISE` with one blocking and one important finding, both about the property round 2
named: a query gives Workflow's answer or none.

- **B1: switching a hook or an fsmonitor program off can change the answer.** An fsmonitor
  program, or an executable `post-index-change` hook, that edits the plan when Workflow's Git
  runs it makes Workflow answer row 4a, while the Controller, having switched it off, answered
  row 2 `BOUND`.
  - **Reproduced** on seeded 2.6.0 targets for both: Workflow in place 4a, the Controller 2.
  - **Census.** With every hook githooks(5) names installed, Workflow's two queries run in place
    at every seed stage (route, publish, generate, ready, revise) fire only
    `post-index-change`. Traced under `GIT_TRACE`, they run only `rev-parse --show-toplevel`,
    `rev-parse --verify`, `config --type=bool core.fileMode`, `ls-files --error-unmatch`,
    `ls-files --others`, `hash-object` and `diff --name-only`.
  - **Fix** (`60fcebd`). Refused before the query runs (`query_git_not_isolated`): an
    executable `post-index-change` hook file in the hooks directory Workflow's Git uses
    (`hook`), a hook any configuration file sets for that event (`hook.<name>.event`), and
    an fsmonitor program (`fsmonitor`: `core.fsmonitor` that `git config --type=bool` does not
    read as a boolean, or `GIT_TEST_FSMONITOR` with it unset). The configuration these checks
    read is Workflow's view, without the Controller's settings. Git's built-in daemon and hooks
    for other events stay switched off.
- **I1: the filter refusals gave `query_failed`.** Fixed in `1f8ec78`: a driver with a program
  gets a Controller probe as its `process`, which leaves a mark in the private directory and
  fails. After the query, whatever it did, a mark refuses it with `query_git_not_isolated`
  (facility `filter`, drivers in `filters`). Found on the way: an empty `process`, as round 2
  set, makes Git skip `clean` altogether, so the probe has to be the `process`. The check no
  longer relies on the query letting a failing Git command fail it.
- **Audit of the rest.** Split-index writes change only how the private index is stored. The
  queries read no signature. No transport only turns a lazy fetch into a failure, and the 2.6.0
  query path catches only Workflow's own drift and unverified errors, never a Git failure.
- **Tests.**
  - `GitIsolationTest` (18 tests): the hook census; each refused hook and fsmonitor variant
    (hooks directory, absolute and relative `core.hooksPath`, operator-configured hook; local,
    operator and environment fsmonitor) shown live in place first; direct-versus-Controller
    regressions for a plan-editing hook and fsmonitor (Workflow 4a, the Controller refuses, runs
    nothing, writes nothing); hooks Git never fires, a non-executable `post-index-change` and
    the built-in daemon still answer `BOUND`; both filter regressions now require
    `query_git_not_isolated`.
  - `RealManagerMigrationTest`: the filter case requires `query_git_not_isolated` for the query
    and the decision; a new case runs the plan-editing fsmonitor and hook on a migrated target
    (Workflow 4c, the Controller refuses both queries and the decision, target unchanged).
  - Each new test fails with its refusal removed (mutation-checked).
- **Local review optional findings.** Resolved: `_query_failed` no longer appends the filter
  note to every failure (`1f8ec78`); the troubleshooting remedy covers a stale index entry
  whose content is unchanged; the installation guide states Git 2.31; the submodule remedy is
  `git submodule deinit`; the guide, ADR and release notes note a split index's timestamp.
- **Docs.** ADR 0006 (a query gives Workflow's answer or none), automation, troubleshooting
  (the new facilities and remedies; `query_failed` no longer covers filters), development (the
  hook census when admitting a release), installation and the 1.3.0 release notes.
- **Verification.**
  - `python3 tools/run_tests.py`: PASS, 2247 tests in 8 shards, exact coverage, 90.7 s, under
    the reaping-subreaper wrapper;
  - `tools/workflow_releases.py check` and `tools/ci_workflows.py --check`: exit 0;
  - `test_packaged_runtime` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`: 9 tests, OK;
  - goldens `--check`: current, except the known 2.5.1 plan-stage `AMENDING_PLAN` difference
    (same exit at the previous head; its permitted-difference test passes).

## Implementation review round 4 -- a boolean fsmonitor under an older Git (manual external review `REVISE`)

The local review approved round 4 with two optional findings. The manual external review (Codex)
returned `REVISE` with one important finding and no blocking one.

- **I1: a boolean `core.fsmonitor` is a program before Git 2.36.** The round-3 check let any
  value `git config --type=bool` accepts through as Git's built-in daemon. Before 2.36, Git runs
  every non-empty value, `true` and `false` included, as the path of an fsmonitor program found
  on `PATH`, and fails on the key written without a value. The installation guide supports Git
  2.31 and later.
  - **Reproduced** with Git built from source: 2.31.0, 2.35.1, 2.35.8 (the last 2.35
    maintenance release, so the boundary is 2.36.0, not the 2.35.1 git-config(1) names) and
    2.36.0, beside the host's 2.55.0. A `true` early on `PATH` that edits the plan, on a bound
    2.6.0 target with `core.fsmonitor = true`, made Workflow in place answer row 4a while the
    Controller answered row 2 `BOUND` under 2.31.0, 2.35.1 and 2.35.8. Both answered row 2
    under 2.36.0 and 2.55.0. Under 2.35.1, `true`, `false`, `yes` and `1` each ran a program of
    that name, an empty value ran none, and the key without a value made Git exit 128.
  - **Fix** (`e91284c`). When `core.fsmonitor` is a non-empty boolean, the Controller asks `git version`,
    which names the query's own Git (the same `git` on the same `PATH`). Before 2.36, or for a
    release that does not start with a major and minor number, it refuses before the query
    runs (`query_git_not_isolated`, facility `fsmonitor`, with the `program` and
    `git_release`). An empty value is no fsmonitor in any release, and from 2.36 a boolean
    stays Git's own daemon, switched off.
  - **Rejected alternative.** Raising the minimum to Git 2.36 would refuse every 2.6.0 query
    under Git 2.31 to 2.35, Ubuntu 22.04's 2.34.1 included, where only this configuration
    changes the answer.
- **Tests.**
  - `GitIsolationTest` (20 tests): a `git` shim that names an older release refuses `true`,
    `false`, `1`, `yes`, a Windows-style release, an unknown release and the key without a
    value, all before the query; an empty value, and `true`/`false` under a 2.36.0 shim, still
    answer `BOUND`;
  - an opt-in live regression (`CONTROLLER_TEST_OLD_GIT`, a Git before 2.36): Workflow in place
    answers 4a, the Controller refuses, runs nothing and writes nothing. It passed with 2.31.0,
    2.35.1 and 2.35.8;
  - with the fix removed, the six shim subtests and the live test fail; with the boundary at
    `(2, 35)`, four shim subtests fail; at `(2, 37)`, the two 2.36.0 subtests error.
  - Run with a real 2.35.8 or 2.31.0 first on `PATH`, `GitIsolationTest` and
    `tests.test_workflow_release_migration` show no other isolation gap. The failures are the
    built-in-daemon test, now refused as intended, and the controls of tests that plant what
    that Git lacks (`GIT_CONFIG_GLOBAL`, from 2.32, and configured hooks).
- **Local review optional findings.** The hook census now runs at all five seeded stages
  (route, publish, generate, ready, revise) in its own class, `QueryHookCensusTest` (`4afb4f3`): it fires
  `post-index-change` at each stage except route, where nothing fires. The two over-refusals
  are verified and documented in troubleshooting, not changed: a `hook.<name>.event` disabled
  by `hook.<name>.enabled = false`, and, with an empty `core.hooksPath` (Git runs no hook),
  an executable `post-index-change` at the worktree's root. Both fail closed.
- **Docs.** ADR 0006, automation, installation (a boolean `core.fsmonitor` needs Git 2.36),
  troubleshooting (the `fsmonitor` facility and remedy with the version condition, and the two
  over-refusals), development (the opt-in) and the 1.3.0 release notes.
- **Verification.**
  - `python3 tools/run_tests.py`: PASS, 2250 tests in 8 shards, exact coverage, 97.1 s, under
    the reaping-subreaper wrapper;
  - `tools/workflow_releases.py check` and `tools/ci_workflows.py --check`: exit 0;
  - `test_packaged_runtime` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`: 9 tests, OK;
  - goldens `--check`: current, except the known 2.5.1 plan-stage `AMENDING_PLAN` difference
    (its permitted-difference test passes).
