# CI2: fresh shard timings and a measured contention estimate, the first slice of the CI follow-ups (Revision 4)

Work item: `workflow-controller-ci2`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `a785d4f8a645f54618779fbf93a58a1f51559165` ("docs: move CI2 up and add RS1 (#372)"), the tip of `main`, passed explicitly as `/milestone-plan a785d4f8a645f54618779fbf93a58a1f51559165`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ci2` (`docs/backlog.yaml` order 95, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ci2.md`. It depends on nothing; CI3 (the three shards and the six-runner cap it plans against) is merged.
Released baseline: the latest tag. The change edits the committed timings, the planner, its tests and pages only, and alters nothing a user sees, so the squash commit is a `ci:` change and publishes no release.
Pull request title: `ci: refresh the shard timings and measure the acceptance-matrix contention (CI2)`

## Goal

CI2 is large and its page says "split at planning". This plan is the first slice. The owner approved fourteen scope items on 2026-11-16 (page section "Scope the owner approved"); by the sizing rule (at most one new mechanism per releasable item) they split into five slices. Every scope item is in exactly one slice except item 1, which is split on purpose: this slice refreshes the timings once by hand and fixes the estimates (a one-off measurement), and ci2d automates the refresh (an after-merge policy, a different mechanism by the sizing rule).

| slice | scope items | mechanism |
| --- | --- | --- |
| **this item, `workflow-controller-ci2`** | 1 (refresh and estimates), 2 (contention) | the planner estimates from measured data |
| `workflow-controller-ci2b` | 3, 4, 7 | run each test once: the duplicate-run removal |
| `workflow-controller-ci2c` | 5, 6, 8, 9, 10 | fixture snapshots and process starts |
| `workflow-controller-ci2d` | 11, 12, 13, and the automation of the timings refresh | after-merge and trigger policy |
| `workflow-controller-ci2e` | [#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322) | a runtime check replacing the static repo-reach heuristic |

Item 14 ("not worth doing") needs no work. This item's one mechanism is **estimates from measured data**: the committed timings are brought up to date, a guard keeps them from going stale unnoticed, and the one measured slowdown the planner ignores (the acceptance matrix, 1.50 times its estimate) is a measured constant instead of a guess. It changes which shard a test runs on, never whether it runs (the planner's invariant I3).

Measured today, on the committed profile at the base commit: the inventory has 1296 atoms and 117 of them (671 tests, including 113 Codex atoms measured at 2,097 s on CI) are absent from `tools/test_timings.json`. An atom with no history costs `tests x` the module's mean seconds per test (`estimate_atoms`), which underestimates the slow Codex tests. The longest shard is 1.08 to 1.50 times the average, about 8 minutes of wall time at three shards.

## What exists

- **The planner.** `tools/test_shards.py`: `estimate_atoms` (known atom: recorded seconds; unknown conformance atom: `DEFAULT_CONFORMANCE_SECONDS`; unknown controller atom: tests times a mean per test), `assign_lanes` and `lane_seconds` (the longer of a shard's two lanes times `LANE_CONTENTION = 1.15` when both lanes have work), `update_timings` (EWMA, `EWMA_WEIGHT = 0.5`) and `CI_MAX_SHARDS = 3`.
- **The refresh route.** `python3 tools/run_tests.py timings merge --into tools/test_timings.json DIR...` folds the `timings-ci-attempt-<a>` artifacts of a CI run into the profile (`docs/guide/development.md`, "Timings").
- **The guard that exists.** `CommittedTimingsTest` in `tests/test_test_shards.py` rejects names that are not in the inventory, a profile not measured on CI, and a multi-test class atom above 200 s (the CI1b ratchet). Nothing rejects missing atoms, by design ("coverage may drift until the next reviewed refresh").

## Decisions

Each decision states one principle.

- **D1. The timings come from real CI, never from a guess or a local run.** The source is the `timings-ci-attempt-<a>` artifact (highest attempt) of the latest runs of `ci.yml` on `main` or on a code pull request that ran the shards, merged with `timings merge`, as CI1b D4 did: two or three runs, oldest first, the count stated in the commit and on the item page. The number of atoms before and after, the 113 Codex atoms and the new `updated_from` digests are recorded. A `main` run that reused a pull request's results (`CI_MAIN_REUSE`) does not qualify: it re-uploads that pull request's records byte for byte, and `update_timings` would fold the same observation twice. Duplicate `shard-*.json` records (same run, same shard, same content) are dropped before `timings merge`, and the count of runs states how many distinct runs were used. If the lane cannot read the artifacts (the artifact download needs an authenticated GitHub call), the checkpoint **stops and reports a blocker**; it never hand-edits the file and never seeds it from a local run (functional review F1 of the adaptive-sharding milestone: a local seed underestimated CI 1.6 times).
- **D2. A ratchet keeps the file from going stale unnoticed, with a limit that ordinary work cannot cross between refreshes.** `CommittedTimingsTest` gains a check: the atoms absent from the committed profile hold at most `MAX_UNTIMED_TEST_SHARE` of the tests of the **atoms the CI plan places on a shard** (the denominator; `main`, `package` and `excluded` atoms are never refreshed by a pull request's records and are not counted). The limit is derived, not chosen: on the whole inventory the untimed share went from near zero at the last full refresh (`065ca1e`, 2026-11-08) to 9.95 percent (671 of 6741 tests) at the base commit, about 1.2 points a day. The refresh is manual until ci2d automates it, so the limit must hold for the longest gap the owner accepts, 14 days, with margin: the constant is **20 percent** and CP1 re-derives it from the CI-placed share it measures (drift per day from the git history since `065ca1e`, times 14 days, rounded up to a multiple of 5 points) and records the numbers on the page. Crossing it then means the refresh is overdue by a clear margin, not that a pull request added a few tests. A negative case beside the check feeds a synthetic profile that omits enough atoms and asserts the failure, one just under the limit that passes, and one that omits only `main`/`package`/`excluded` atoms and passes (the denominator), as the CI1b ratchet does. The check reads only the committed file and the inventory, so it is deterministic. The automation that makes the limit unreachable belongs to ci2d.
- **D3. The acceptance-matrix slowdown is a measured constant, measured at the shard.** The planner multiplies a shard's expected duration by one `LANE_CONTENTION` (1.15) when both lanes have work, but the shard holding the acceptance matrix runs about 1.50 times slower than estimated. That 1.50 is a shard-level figure, so the measured quantity is too: for each shard of the runs D1 uses, the observed `wall_seconds` (from the `results-*` artifact) over the **uncontended** estimate, `max(lanes)` of that shard's plan, recomputed from the plan's atoms with the profile CP1 commits (the `test-plan` artifact names the atoms of each shard). The ratio is a calibration of the planner's estimator **using the refreshed profile**, not a quantity independent of the refresh: `update_timings` folds the very observations of D1's runs into the profile that feeds the denominator, so the fit is partly against data the profile already contains. The shard-level calculation keeps it from comparing a recorded second to itself (the denominator is the planner's `max(lanes)` over the shard's atoms, the numerator the shard's observed wall time), and the page states that the fitted value is in-sample. The ratio is taken separately for shards that hold the matrix and shards that hold no conformance suite or hold other suites, so the matrix shard's factor is compared with the global one on the same runs. Where the matrix shard's ratio is stably above 1.15 in at least two runs, it is recorded in a named constant (`CONFORMANCE_CONTENTION`, suite key to factor, each value with the runs it came from in a comment) that `lane_seconds`/`assign_lanes` apply **in place of** the global factor, never on top of it, for a shard that holds that suite, has Controller atoms and has conformance atoms (both lanes have work; a shard with the matrix and no Controller atoms is a single lane and takes no factor). Double counting: recorded seconds of Controller atoms were observed beside the matrix, so some contention is already inside them; the factor is fitted against `max(lanes)` computed from those same recorded seconds, so whatever they already contain is absorbed into the fitted value rather than added, and the page states this. The matrix is the only suite expected to qualify; any other suite stays on the global factor. Alternatives rejected: keeping the matrix on a shard of its own (three shards cannot spare one: the controller lane's roughly 79 minutes would then run on two), and a per-atom factor stored in the timings file (the file stays a plain record of seconds, so the merge and the schema do not change).
- **D4. Estimates never add, drop or condition a test.** The key set of `estimate_atoms` and the plan's selected test ids are unchanged by D2 and D3 (I3). A test asserts it: plans built over the same selection with and without `CONFORMANCE_CONTENTION` have identical `selected_ids` and identical test-id sets across shards.
- **D5. The pull request's own CI is the proof, and the page says so.** The change touches `tools/` and `tests/`, so every shard runs. The checkpoint that finishes the item records the planner's estimated longest shard against the average before and after from the plans CI itself builds: `python3 tools/run_tests.py plan --profile ci --ci-placement --output <file>`, the exact selection and parameters of the `plan` job (`.github/workflows/tests.yml`), never a plan without `--ci-placement`, which also places the `main`, `package` and `excluded` atoms and overstates the longest shard (8,175 s against 3,260 s on the files at the base commit). The planner balances exactly the numbers it reports, so two self-balanced plans always show a longest shard of about 1.00 times the mean (3,260.066, 3,260.067 and 3,260.067 s at the base commit) and comparing them shows only the estimator's level, never a gain. The predicted gain is therefore the **base plan rescored with the after estimator**: save the plan at the base commit (`--ci-placement --output`, before CP1's refresh), keep its shard atom sets unchanged, and score each shard with the after estimator (the refreshed profile plus `CONFORMANCE_CONTENTION`, through `estimate_atoms` and `lane_seconds`, in a throwaway computation that is not committed). The page records that rescored plan's longest shard and mean against the new plan's longest shard and mean (the plan saved after CP2, from `shards[].estimate_seconds`); the difference between the rescored base plan's longest shard and the new plan's longest shard is the predicted gain. It also records, for the runs D1 uses, each shard's observed `wall_seconds` next to its estimate, before (the base estimator) and after (the after estimator), since the imbalance the goal names (1.08 to 1.50 times the average) is observed on CI and not visible in the planner's own plan. Once the pull request's CI has run, the elapsed CI time is the proof, measured as one interval for both sides (`run_started_at` to the `completed_at` of `tests-result`) against the 35 to 41 minute baseline. The page states the result either way; a gain smaller than the planner predicts is reported, not argued away.
- **D6. The page and the guides change with the behaviour, and the backlog is split in place.** `docs/guide/development.md` (the "Timings" paragraph and the ratchet paragraph), `docs/guide/ci-and-releases.md` (the estimate paragraph, both places that state the 1.15 rule, at the lane-contention sentence near line 164 and the one near line 317, the refresh date, and the sentence that calls the acceptance matrix the floor of the longest shard, which the measurements contradict: the controller lane is always the longer one) and `docs/adr/0005-adaptive-test-sharding.md` (a dated amendment beside the 2026-10-13 one: the estimate rule "the longer of its two lanes, times 1.15 when both have work", line 73, now takes the per-suite factor for a shard holding the matrix) are updated in place. The CI1b review follow-ups of [#324](https://github.com/RodrigoFAbreu/workflow-controller/issues/324) are a documentation change and are done here: reword the pre-plan summary on the CI1b item page, say "blends" for the next merge, add the two missing D4 rule parts and the "base without test methods" wording to the development guide, and record the fixture keys and the derivation inputs. The backlog gains the four follow-up items of the table above (ci2b to ci2e) directly after `workflow-controller-ci2` (every later item's `order` is renumbered by the number added, since orders are unique contiguous integers; each new item carries `depends_on: [workflow-controller-ci2]`), each `next`, with a page that states its scope items from the owner's list; [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target) stays open and is referenced by every slice, to be closed by the last slice to merge; `workflow-controller-ci2` is retitled to this slice and `docs/ROADMAP.md` is regenerated. The backlog file is edited by this checkpoint, never by `/milestone-plan`.

## Open decisions

No repository decision-record "Open decision" row is touched. The measurement of D3 may find that no suite besides the matrix qualifies, or that the matrix's factor is not stable across runs; then the constant is not added, the page records the measured ratios, and the checkpoint stops for the owner rather than inventing a number.

## Out of scope

The duplicate runs (ci2b), the fixture snapshots and process starts (ci2c), the after-merge and trigger policy and the automation of the timings refresh (ci2d), the runtime reach check of #322 (ci2e), `CI_MAX_SHARDS` (the owner's CI3 choice), and the timing flakes (FX1).

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Refresh the committed timings and add the staleness ratchet | - | 2 | 1 |
| CP2 | Measure the acceptance-matrix contention and apply it in the planner | CP1 | 2 | 1 |
| CP3 | The guides, the item pages and the backlog split | CP2 | 1 | 1 |

<!-- CP1 -->
### CP1 -- Refresh the committed timings and add the staleness ratchet

D1, D2. Download the latest retained `timings-ci-attempt-<a>` artifacts (two or three runs, as D1 says), merge them oldest first into `tools/test_timings.json` with `python3 tools/run_tests.py timings merge --into tools/test_timings.json DIR...` and record the atom counts before and after. Add `MAX_UNTIMED_TEST_SHARE` and the staleness check, with the denominator and negative cases D2 names, to `CommittedTimingsTest`, and record the measured share and the derivation of the limit. The test also asserts the multi-test class ratchet (200 s) still passes on the refreshed file; a class the refresh pushes over it is reported as a blocker, since splitting a class belongs to ci2b.

**Files:** `tools/test_timings.json`, `tests/test_test_shards.py`

**Tests:** `tests.test_test_shards` (class `CommittedTimingsTest` first), and, to see the shard balance, `python3 tools/run_tests.py plan --profile ci --ci-placement --output <file>` saved at the base commit before the refresh, which is the plan D5 rescores with the after estimator (the `plan` subcommand has no `--plan-only`); two self-balanced plans are not compared as the predicted gain.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- Measure the acceptance-matrix contention and apply it in the planner

D3, D4. Measure the shard-level observed-over-uncontended ratios from the artifacts of the CP1 runs as D3 says; add `CONFORMANCE_CONTENTION` to `tools/test_shards.py` and apply it in `lane_seconds` and `assign_lanes`, keeping the global `LANE_CONTENTION` for every other suite. Tests: a shard holding the matrix is estimated with its measured factor and another shard with the global one; a suite not in the mapping is unchanged; plans built with and without `CONFORMANCE_CONTENTION` have identical `selected_ids` and test-id sets (D4); the factor applies only when the shard has matrix-suite conformance atoms and Controller atoms, and a shard with the matrix and no Controller atoms takes no factor; the plan digest is deterministic. Update the pinned numbers in `tests/test_test_shards.py` and `tests/test_scenarios.py` that the new estimates move, and nothing else.

**Files:** `tools/test_shards.py`, `tests/test_test_shards.py`, `tests/test_scenarios.py`

**Tests:** `tests.test_test_shards`, `tests.test_scenarios`.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The guides, the item pages and the backlog split

D5, D6. Update `docs/guide/development.md`, `docs/guide/ci-and-releases.md` (both 1.15 passages) and add the dated amendment to `docs/adr/0005-adaptive-test-sharding.md` as D6 says and make the #324 wording changes on `docs/items/workflow-controller-ci1b.md`. Record the base plan's longest-shard and average estimates rescored with the after estimator against the new `--ci-placement` plan's (D5: the predicted gain, never two self-balanced plans compared), each shard's observed `wall_seconds` against its estimate before and after, the elapsed-CI comparison and its reporting, and the measured ratios (D3) on `docs/items/workflow-controller-ci2.md`, retitled to this slice with the other slices' scope pointing at the new item pages `docs/items/workflow-controller-ci2b.md` to `docs/items/workflow-controller-ci2e.md` (each one page, each naming its scope items and its sources). Add the four backlog items after `workflow-controller-ci2` in `docs/backlog.yaml` and renumber the later `order` values, and regenerate `docs/ROADMAP.md` with `python3 scripts/workflow_backlog.py generate`.

**Files:** `docs/guide/development.md`, `docs/guide/ci-and-releases.md`, `docs/adr/0005-adaptive-test-sharding.md`, `docs/items/workflow-controller-ci1b.md`, `docs/items/workflow-controller-ci2.md`, `docs/items/workflow-controller-ci2b.md`, `docs/items/workflow-controller-ci2c.md`, `docs/items/workflow-controller-ci2d.md`, `docs/items/workflow-controller-ci2e.md`, `docs/backlog.yaml`, `docs/ROADMAP.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-ci2`.
<!-- /CP3 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | The committed timings are measured on CI and cover the inventory, and a ratchet fails when the untimed share grows past its limit | CP1 |
| R2 | The planner applies a measured contention factor to the acceptance matrix and leaves every other suite and every selected test unchanged | CP2 |
| R3 | The guides, the item pages and the backlog state the result, the measured effect and the split into ci2b to ci2e | CP3 |
