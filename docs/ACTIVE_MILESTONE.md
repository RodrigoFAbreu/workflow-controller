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
- **CP2 -- pending.** Documentation and roadmap.

## Next action

`/milestone-implement workflow-controller-installed-release-test-agnostic` for CP2.
