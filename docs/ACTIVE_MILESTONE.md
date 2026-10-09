# Active Milestone

## Status

**In progress.** `workflow-controller-installed-release-test-agnostic` (`docs/ROADMAP.md` step C9d,
follow-up 16), plan revision 2, base commit `5532cdd`. The plan is
`docs/ai-workflow/CONTROLLER_INSTALLED_RELEASE_TEST_PLAN.md`;
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of the phase.

- **CP1 -- complete (verified).** `InstalledReleaseTest` in `tests/test_workflow_releases.py` now runs the
  three non-Manager steps of `managed_repo.inspect` (read the record, the `RELEASE_CONTRACTS` arm, the
  profile check) on the real repository, and checks every installed file against the `sha256` and
  `executable` flag in `.workflow-manager/installation.json` plus the installed command set. No vendored
  tree and no version literal. `InstalledReleaseRefusalTest` shows a modified, missing, extra and
  mode-changed file, a release without the protocol script, a protocol-major-2 `describe` and an
  unsupported profile each refused, on a disposable installation. Checked with `tests/workflow_releases/2.9.0/`
  deleted from a copy: the new tests pass, the old ones fail. `python3 -m unittest tests.test_workflow_releases
  tests.test_managed_repo` passes (the live-Manager test is skipped here).
- **CP2 -- complete (verified).** `docs/guide/development.md` now says the installed tree needs no vendored tree and is checked by capability and `installation.json`; `docs/ROADMAP.md` marks C9d and follow-up 16 complete and the Next list names C8. `docs/compatibility.md` and `docs/update.md` state no vendored-tree requirement, so are unchanged. `tools/check_docs.py` passes; the full suite (`tools/run_tests.py --jobs 2`, 3095 tests) fails only the two Git 2.56 trailer tests (follow-up 12).

- **Self-review -- complete (2026-10-10).** The full milestone diff matches D1-D6: the test's admission
  follows `managed_repo.inspect`'s non-Manager steps in order, no `controller/` change, and the vendored-tree
  tests are unchanged. No defects found. `tools/check_docs.py` and `tools/workflow_releases.py check` exit 0;
  `tools/run_tests.py` (3095 tests, 8 shards) fails only the two Git 2.56 trailer tests (follow-up 12).

## Next action

Implementation review of the generated bundle (`.ai-review/workflow-controller-installed-release-test-agnostic/`).
