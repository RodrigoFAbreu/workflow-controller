# Active Milestone

## Status

**Implementing.** `workflow-controller-trunk-branch-pr-release-orchestration`, governing workflow
version `2.2`, base commit `00c3b7136dae2ca1993af91c999df48ad376f433` ("Prepare the 1.1.1 patch
release"). Plan revision 10 was approved by both plan-review stages (approval commit `bb7839a`).
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for the phase and for each checkpoint's
status. The installed release Controller 1.1.1 orchestrates this milestone, under the installed
Workflow 2.5.1.

## Goal

Establish a reusable, lightweight trunk-based development and release model, with
`workflow-controller` as its first adopter:

- **generic capability** that any Workflow-managed repository can reuse: milestone branch binding,
  a Controller-owned GitHub boundary, the Draft PR lifecycle, trunk-drift detection, a repository
  release policy, and a crash-safe release transaction with explicit collision and recovery
  states;
- **reference configuration** for this repository only: trunk `main`, branches
  `milestone/<work-item-id>`, version source `pyproject.toml`, tag `v{version}`, a wheel plus
  `SHA256SUMS` published as a GitHub Release;
- **one version authority**, `pyproject.toml`'s static `[project].version`;
- **release on version change**: a push to `main` releases exactly when the version is new. The
  tag is created only after validation, at the validated commit, and is never moved;
- **Workflow stays authoritative**: under Workflow 2.5.1 the Controller detects trunk drift and
  fails closed at readiness. Binding to Workflow 2.6.x is a separate follow-up milestone.

## Plan

- Plan: `docs/ai-workflow/CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md`
- Registry: `docs/ai-workflow/registry/workflow-controller-trunk-branch-pr-release-orchestration-registry.json`
  (checkpoints `CP1`-`CP10`; `CP10` is terminal)
- Requirement map: `docs/ai-workflow/requirements/workflow-controller-trunk-branch-pr-release-orchestration-mapping.json`

The previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-release-runtime-observability.md`. Its deferred
follow-ups are listed there and in `docs/ROADMAP.md` section 1.4. None of them is in this
milestone's scope.

## Baseline

Recorded at CP1 start, before the first edit, on a detached worktree at `bb7839a`:
- `python3 -m unittest discover -s tests -t .` (no `PYTHONPATH=.`): 1275 tests, OK (6 skipped),
  98 s;
- the seven conformance suites, from `scripts/`: all OK. `workflow_fingerprint_test.py` 218,
  `workflow_integration_test.py` 260 (1 skipped), `workflow_acceptance_matrix_test.py` 146
  (18 skipped), `workflow_state_completion_obligations_test.py` 106,
  `workflow_fingerprint_generalization_test.py` 79, `workflow_test_harness_test.py` 19.
  `workflow_state_test.py`'s summary line was not captured in the baseline run: its captured
  output tail shows its regression checks passing, and the whole run exited 0;
- `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime`: 9 tests,
  OK.

## Checkpoint progress

- **CP1 -- single version authority: complete.**
  - `pyproject.toml` declares `version = "1.1.1"` statically. `dynamic` and
    `[tool.setuptools.dynamic]` are gone.
  - `controller/version.py` holds no value. It is a stdlib-only resolver that imports nothing from
    `controller`:
    - `source_version(code_root)` reads `<code_root>/pyproject.toml`'s `[project].version`;
    - `package_version(code_root)` reads the one `workflow-controller` distribution installed in
      `code_root` itself whose `RECORD` lists `controller/__init__.py`;
    - `parse_pyproject_version` is shared with `handoff`.
    All three raise `ValueError` naming the failure. `version.SEMVER_RE` duplicates
    `buildinfo.SEMVER_RE`, since neither leaf may import the other; a test pins them equal.
  - `identity.resolve_runtime` carries a `version` on `RuntimeResolution`:
    - `package`: the distribution metadata version. It must equal `BUILD_INFO.json`'s version,
      and a disagreement is `unidentified` ("partially upgraded"). Missing metadata is
      `unidentified` too;
    - `source`: `pyproject.toml`'s version. An unreadable version is `unidentified`;
    - `unidentified`: whichever reader succeeds, else `unknown`.
  - `ControllerIdentity.version` is a required keyword field with no import-time default.
    `materialise` records the snapshot's own version in `SOURCE_PIN.json` and in the
    `controller_runtime` block: a source snapshot's own `pyproject.toml`, or a package snapshot's
    own `controller/BUILD_INFO.json`. `pin()` reads a pinned child's version only from
    `SOURCE_PIN.json`. A missing or non-semver value raises `SourceSnapshotError`
    (`raised_by: pin`), with no fallback.
  - `cli.version_text` prints the resolved runtime's version (`unknown` if the identity cannot be
    resolved). `--version` line 1 is unchanged in form.
  - `handoff` reads the approved version from `HEAD:pyproject.toml` with `tomllib`.
  - `setup.py`'s build hook takes the version from `self.distribution.get_version()`.
  - `tools/release.py`: `version`, `verify-tag` and `verify-wheel` read
    `version.source_version(<repository root>)`. `main()` takes a `repo_root` keyword.
    `verify-tag` now requires the static form, and refuses a dynamic version or a
    `[tool.setuptools.dynamic]` version.
  - Tests:
    - new `tests/test_version_authority.py` (19 tests) covers the single authority, the readers,
      a stale `egg-info` and another distribution on `sys.path` being ignored by a source runtime,
      the package metadata version, metadata/BUILD_INFO disagreement, missing metadata, pinned
      source and package children, pin-version refusal, a dirty snapshot's own version, and
      handoff's committed read. It runs in a new `trunk` CI shard; `.github/workflows/validate.yml`
      was regenerated;
    - `test_release_tools.CheckedOutVersionTest` rewrites the version in a disposable checkout and
      sees `version`, `verify-tag` and `verify-wheel` all follow it. `verify-tag`'s refusals are
      rewritten for the static form;
    - `test_packaged_runtime` case 6 now also compares `tools/release.py version`.
      `_write_version` rewrites `pyproject.toml`;
    - `fixtures.CONTROLLER_VERSION` replaces every test's `version.__version__`.
      `fixtures.build_package_tree` writes a `*.dist-info` by default (`metadata_version`,
      `dist_info`). Every test `ControllerIdentity` passes `version=`.
  - README: the two pointers to `controller/version.py` now name `pyproject.toml`. The full
    releasing rewrite is CP10's.
  - Two existing tests were adjusted, not weakened. The pathspec-dirty test appends to
    `pyproject.toml` rather than replacing it, since a checkout with no version is now
    `unidentified`. The "pin without `runtime_kind`" test keeps `version`, since every pin carries
    it and there is no fallback.
  - Verified:
    - narrow: `tests.test_version_authority`, `tests.test_identity`, `tests.test_release_tools`,
      `tests.test_buildinfo`, `tests.test_packaged_runtime` (packaging required), `tests.test_cli`,
      `tests.test_handoff`, `tests.test_job`, `tests.test_job_validation`,
      `tests.test_package_structure`, `tests.test_ci_workflows`,
      `tests.test_plan_document_consistency`, all OK;
    - full: `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest discover -s tests -t .`
      ran 1295 tests, OK (6 skipped), in 99 s. An earlier full run caught one real failure:
      `test_write_containment`'s package-wide scan flags any `.replace(...)` call, and
      `package_version` normalised paths with `str.replace`. It now uses `PackagePath.as_posix()`;
    - the seven conformance suites, all OK: `workflow_fingerprint_test.py` 218,
      `workflow_state_test.py` 853, `workflow_test_harness_test.py` 19,
      `workflow_integration_test.py` 260 (1 skipped), `workflow_acceptance_matrix_test.py` 146
      (18 skipped), `workflow_state_completion_obligations_test.py` 106,
      `workflow_fingerprint_generalization_test.py` 79;
    - `python3 tools/ci_workflows.py --check`: passes.
