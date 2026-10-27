# Active Milestone

## Status

**Implementing.** `workflow-controller-scenario-base` (SC1, issue
[#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188)): the scenario tests measure from the checkout and a
sandbox-free guard checks the fixture's classification. Plan revision 1 approved by gate policy (6721ebd).

- CP1 (pin the base to the checkout, guard the classification): complete. `tests/test_run_scenarios.py` gains
  `pin_base_to_checkout`, `fixture_files`, `FixtureClassificationTest` and `PinBaseToCheckoutTest`; `tests.test_run_scenarios`
  (53 tests) and `tests.test_catalogue_determinism` (11 tests) pass on a machine whose probe passes.
- CP2 (guide and roadmap): complete. The development guide describes the pinned base and the guard; ROADMAP row SC1 is marked complete and H3 is next.

## Next action

Every checkpoint is complete. Run `/milestone-implement workflow-controller-scenario-base` again to self-review, verify and prepare the implementation review bundle. After acceptance, H3 is next.

## Functional review checklist

`workflow-controller-scenario-base` (SC1, #188), implementation revision 1. Only tests and one guide paragraph change;
there is no new command or user-visible behaviour, so every flow is a test or inspection run offline from the checkout.
The flows need no network, no real GitHub, `claude`, `codex` or `ssh`, and write nothing inside the checkout.

**Setup.** Check out this branch (tip at or after the evidence commit named below). Python 3 with the repository's
test dependencies; no feature flags. Use a scratch `TMPDIR`. Run from the repository root with a plain
`python3 -m unittest` (no `PYTHONPATH=.`, no `FORCE_COLOR`).

**Test data.** None to seed. The pinned work item is `workflow-controller-automated-lifecycle-scenarios`
(`docs/ai-workflow/WORKFLOW_STATE.json`); its artifacts file must stay byte-unchanged.

**Flows.**

1. Guard and helper tests (no sandbox needed):
   `python3 -m unittest tests.test_run_scenarios.FixtureClassificationTest tests.test_run_scenarios.PinBaseToCheckoutTest -v`.
   Expected: 3 tests pass (every committed fixture file classifies; a made-up `unlisted-top-level-entry/file.txt` is
   reported by name; the base equals the repository's `HEAD` for a missing, an old and the same commit).
2. The three tests named in the Goal, where the sandbox probe passes:
   `python3 -m unittest tests.test_run_scenarios.IngestionTest.test_records_are_accepted_with_head_and_identity_asserted_separately tests.test_run_scenarios.SubsetDeterminismTest.test_two_subset_runs_agree_on_every_normalised_summary tests.test_catalogue_determinism.CatalogueDeterminismTest.test_two_complete_runs_agree_on_every_normalised_summary`.
   Expected: pass, no `UnclassifiedPathError: .claude/settings.json`. If the sandbox probe fails on your machine they skip
   locally; that is the existing behaviour, not a finding.
3. Whole modules: `python3 -m unittest tests.test_run_scenarios tests.test_catalogue_determinism`. Expected: green
   (53 and 11 tests on a machine whose probe passes).
4. The guard guards: temporarily (in a scratch copy of the repository, never in the checkout) add a file under a new
   top-level entry listed in `kit.CODE_PATHS`, or no such entry exists to add, so instead run flow 1 after adding an
   untracked file under an existing code directory that the declaration neither lists nor excludes. Expected:
   `test_every_file_the_fixture_commits_is_classified` fails naming that path.
5. Shallow-checkout independence: `git clone --depth 1 file://$PWD /scratch/shallow`, copy the working tree over it as
   in flow 4's scratch copy, and run flow 1 there. Expected: the guard and helper tests still pass (they need neither
   the sandbox nor the old base commit).
6. Probe forced to fail (existing tests): `python3 -m unittest tests.test_run_scenarios -k Probe -k Skip -k CI`.
   Expected: the existing local-skip and CI-failure unit tests stay green; the guard still runs.
7. Documentation: read the "The fixture's base and the classification guard" paragraph in `docs/guide/development.md`
   ("Lifecycle scenarios"); it must match flows 1-5. `python3 tools/check_docs.py` passes. `docs/ROADMAP.md` row SC1 is
   marked complete and H3 is next.
8. Scope: `git diff 74a494b..HEAD --stat -- controller tools` is empty, and the pinned item's artifacts file is
   unchanged (`git diff 74a494b..HEAD -- docs/ai-workflow/*automated-lifecycle-scenarios-artifacts.json` is empty).

**Known limitations / out of scope.** No change to `controller/`, `tools/run_scenarios.py` or any scenario; the CI
sandbox probe itself is not made to pass; the Workflow's default declaration template does not list
`.claude/settings.json` (a Workflow-repository issue, D3); nothing is released (`test:` squash commit).
