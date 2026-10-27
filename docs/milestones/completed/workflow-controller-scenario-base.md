# SC1: the scenario tests measure from the checkout, and CI guards their classification (Revision 1)

Work item: `workflow-controller-scenario-base`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `74a494b89b06fbeb98df1d344b3f5a95077f209f` ("chore: move this repository to Workflow 2.22.0 and queue SC1 (#188) before H3 (#189) (#188)"), the tip of `main`, passed explicitly as `/milestone-plan 74a494b…`.
Lifecycle authority: this repository's installed Workflow **2.22.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row SC1, next after H2 (owner decision, 2026-10-27); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188).
Released baseline: `workflow-controller 1.22.2`. Only tests and a guide change, so the squash commit is a `test:` change and releases nothing (D5).
Pull request title: `test: measure the scenario tests from the checkout and guard their classification (SC1, #188)`

## Goal

Three scenario tests error on every machine whose sandbox probe passes (`tests.test_run_scenarios.IngestionTest.test_records_are_accepted_with_head_and_identity_asserted_separately`, `tests.test_run_scenarios.SubsetDeterminismTest.test_two_subset_runs_agree_on_every_normalised_summary`, `tests.test_catalogue_determinism.CatalogueDeterminismTest.test_two_complete_runs_agree_on_every_normalised_summary`), with `workflow_fingerprint.UnclassifiedPathError: .claude/settings.json`. Where the sandbox probe fails, `CloneCase` skips its classes locally, while under `CI` the catalogue test (`CatalogueDeterminismTest.setUpClass`, `tests/test_catalogue_determinism.py`) and the generated scenario tests (`tests/test_scenarios.py`) fail on an unavailable probe instead; that failure behaviour is deliberate, is covered by existing tests, and this milestone preserves it. This plan does not rely on any claim about why CI has not shown the error.

The cause is in the fixture, not in the product. `CloneCase.setUpClass` (`tests/test_run_scenarios.py`) commits the working tree into a clone and leaves the pinned work item's `base_commit` (`workflow-controller-automated-lifecycle-scenarios`) as recorded in `WORKFLOW_STATE.json`. The Workflow's implementation-stage identity is computed over `base_commit..head`, so every file added to the repository since that old commit is part of the diff and must be classified by that old item's artifacts declaration. `.claude/settings.json` (added by #171) is not listed there, so the next added file of any unlisted kind does the same.

This milestone makes the fixture independent of the history since the pinned base, and adds a guard CI can run without the sandbox. One concern only: **what the scenario fixture measures its identity from, and the proof that what it measures is classified.** Real Controller runs are unaffected (their base commit is after the file).

## Decisions

Each decision states one principle.

- **D1. The fixture measures from the checkout, always.** `CloneCase.setUpClass` sets the pinned item's `base_commit` in the clone's copy of `WORKFLOW_STATE.json` to the clone's own `HEAD` (the checkout's tip, before the working-tree commit), unconditionally. Today it does so only when the recorded base is absent (CI's shallow checkout), so the full-history case, the one developers run, is the one that breaks. The diff the identity is computed over is then exactly the working tree's changes to `kit.CODE_PATHS` plus the copied state file, which the pinned item's declaration classifies (D2). The code that does this moves into one module-level helper, `pin_base_to_checkout(state, clone)`, so a test can call it without a sandbox.
- **D2. A guard proves the fixture's classification, without the sandbox.** A new test class in `tests/test_run_scenarios.py` that does not derive from `CloneCase` (so it needs neither the sandbox nor the pinned base, and runs in every environment, including a shallow CI checkout where the fixture's fallback repin would otherwise hide an unclassified file) loads the pinned item's `*-artifacts.json` and the Workflow's `classify_path_implementation_stage` from `scripts/`, and requires that every file under `kit.CODE_PATHS` that the fixture would copy (listed with the fixture's own `git ls-files -co --exclude-standard` call, through one shared listing helper, so the guard measures exactly what `CloneCase.setUpClass` commits), and `docs/ai-workflow/WORKFLOW_STATE.json`, classifies without `UnclassifiedPathError`. A new top-level entry in `kit.CODE_PATHS`, or a file the declaration does not cover, then fails by name in every environment, including a shallow CI checkout, instead of erroring only on a developer's machine with full history. A second test calls `pin_base_to_checkout` on a one-commit temporary repository and requires the state's base to equal that repository's `HEAD`, whatever it held before.
- **D3. History is not edited, and the template is not ours.** The pinned item's artifacts file is a completed work item's record and stays byte-unchanged. `.claude/settings.json` is also missing from the Workflow's default declaration template (`workflow_state.generate_artifacts_declarations` lists `.claude/commands/` only), so an item that edits `.claude/settings.json` would fail its own implementation bundle with the same error; that template belongs to the Workflow repository and the fix is an issue there (filed by the owner; named in the pull request body), not an edit here. This milestone does not depend on it, because after D1 no scenario run measures a file outside `kit.CODE_PATHS`.
- **D4. The scenario tests keep their meaning.** The tests assert that a stored record's `head` is the clone's HEAD and its `identity` equals `approval_review_content_id(..., base_commit=item["base_commit"], head=...)`; both sides read the same pinned value, so they stay a real comparison. No scenario, record format, runner option or catalogue entry changes, and `controller/` is not edited.
- **D5. Nothing a user sees changes, so nothing is released.** Only `tests/` and `docs/guide/development.md` change, plus the bookkeeping `docs/ROADMAP.md` and `docs/ACTIVE_MILESTONE.md` that this item's declaration excludes: the squash commit is `test:`, which `.workflow-controller/policy.json` maps to no release.

## Out of scope

A change to `controller/`, to `tools/run_scenarios.py` or to any scenario; making the sandbox probe pass on the CI runner; editing the pinned item's artifacts file or any other completed item's; the Workflow template for `.claude/settings.json` (D3).

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Pin the base to the checkout and guard the classification | - | 1 | 1 |
| CP2 | The guide and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- Pin the base to the checkout and guard the classification

**Files:** `tests/test_run_scenarios.py`

D1, D2, D4. In `tests/test_run_scenarios.py`: the module-level `pin_base_to_checkout(state, clone)` helper, called by `CloneCase.setUpClass` in place of the shallow-only probe; the non-sandbox guard class (every `kit.CODE_PATHS` file the fixture copies, listed by the shared helper, and the state file classify under the pinned item's implementation-stage declaration); the helper test on a temporary one-commit repository.

Tests:
- the helper test: the base equals the repository's `HEAD` for a state holding a missing commit, an old commit and the same commit;
- the guard test passes on this tree and, with a made-up unlisted path appended to the file list in the test, fails naming that path (so the guard is shown to guard);
- on a machine where the probe passes, the three tests named in the Goal pass (`python3 -m unittest` of each), and the whole of `tests.test_run_scenarios` and `tests.test_catalogue_determinism` stay green;
- with the probe forced to fail (the sandbox-unavailable condition), the guard still runs, the `CloneCase` classes still skip locally, and with `CI` set the catalogue and generated scenario tests still fail on the unavailable probe (the existing CI-failure and local-skip unit tests stay green).
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guide and the roadmap

**Files:** `docs/guide/development.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D3, D5. `docs/guide/development.md` (the "Lifecycle scenarios" section): the fixture measures from the checkout's tip, so an added file cannot reach the Workflow's classification through an old base, and the CI guard checks the classification without the sandbox. `docs/ROADMAP.md` row SC1 marked complete; `docs/ACTIVE_MILESTONE.md` points at H3. `python3 tools/check_docs.py` passes.
<!-- /CP2 -->
