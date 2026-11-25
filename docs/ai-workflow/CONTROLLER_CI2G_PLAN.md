# CI2g: shard affinity for tests that share a snapshot (Revision 2)

Work item: `workflow-controller-ci2g`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `861c845384f774aaec5f42b60ab4f4de6d84a05e` ("fix: retry before the false release-without-tag collision gate (RR1) (#389)"), the tip of `main`, passed explicitly as `/milestone-plan 861c845384f774aaec5f42b60ab4f4de6d84a05e`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ci2g` (`docs/backlog.yaml` order 104, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ci2g.md`. It depends on `workflow-controller-ci2c`, which is accepted.
Released baseline: the latest tag. The change edits `tools/`, tests and pages and no `controller/` module, so the squash commit is a `ci:` and publishes no release.
Pull request title: `ci: place tests that share a snapshot on one shard (CI2g)`

## Goal

A lifecycle snapshot is kept per test process (`fixtures.take_snapshot`, `fixtures.restore_snapshot`), so a row is driven once on every shard that runs a test reaching it. CI2c cut the keys to what each drive reads, and its counters showed the saving is small until the tests that reach one row sit on one shard: `to_38e` was built 14 times and restored 24 times over the shard processes of one run, where one build and 37 restores would do. The planner (`tools/test_shards.py`) knows nothing about snapshots: it balances whole test classes by estimated seconds only.

The fix is one mechanism: **the planner prefers to put the classes that reach the same snapshot row on one shard.** The preference is bounded so it can never unbalance the shards, and it is advisory like the timings: it decides where a class runs, never whether it runs. The owner's estimate is about one minute of pull request time.

## What exists

- **The unit of placement is an atom** (`tools/test_shards.py`): a test class, a module with a module fixture, or a conformance suite. `build_plan` estimates each atom, takes the shard count from `shard_count` and places whole atoms with `assign_lanes` (two-lane, conformance atoms first) or `assign_atoms` (plain LPT). `validate_plan` proves the plan partitions the selection into whole atoms in canonical order. `EXCLUSIVE_ATOMS` is the existing registry keyed by atom, with its own validation in `build_inventory`.
- **The key is made at run time.** `Lifecycle.reach(name, drive, late=...)` (`tests/test_protocol_lifecycle.py`) builds `(name, inputs)` and calls `fixtures.restore_snapshot`/`take_snapshot`; `_ReviewCase.setUp` (`tests/test_review_job.py`) goes through `reach`. The classes that reach a row are the test classes of `tests/test_protocol_lifecycle.py`, `tests/test_protocol_job.py`, `tests/test_functional_codex_route.py`, `tests/test_review_job.py` and the modules that subclass its case. Nothing records which class reaches which key: `fixtures.snapshot_counts()` is keyed by row name only, and `WFC_SNAPSHOT_REPORT` writes those counts per process.
- **A count is per process.** Each shard process builds a row once whatever the key, and `tests.test_functional_lifecycle` is excluded from the shard matrix by `CI_PLACEMENT`, so only the modules the shards run matter here.
- **CI plans from `tools/test_timings.json`** and the plan job and every shard compute the same plan from the same inputs, so a planner change needs no new plan key as long as the digest is a function of the shards.

## Decisions

- **D1. The classes that reach a key are measured, then declared.** A group is a **snapshot key**, not a row: snapshots are kept per key, so two inputs of one row share no tree and are two groups. `fixtures` records, with `WFC_SNAPSHOT_REPORT` set, the test ids that reached each key through a drive that can share a snapshot (`Lifecycle.reach` passes its test), so a measurement run names the classes per key. A key is labelled by its row name plus a short content digest of the whole key (a hash of its canonical text, never Python's `hash()`, so the label is the same in every process and run). The result is committed as a table in `tools/test_shards.py`: `SNAPSHOT_AFFINITY`, a mapping from a group name (that key label) to a tuple of atom keys. A declared table, not a runtime scan: the planner stays a pure function of the inventory, the timings and this table, and a plan stays byte-identical in every process.
- **D2. An atom is in at most one group.** A class that reaches several keys is declared in the group of the key that the most of its test ids reached (CP1 records exactly those test ids, so the count is a number of ids per key and per class), ties broken by the lower key label; the table is the single place that says so. Test ids that reached a key only under the scenario kit are not recorded (CP1), so a class that reaches a key only under the kit is in no group: it gains nothing from co-location. A group has at least two atoms, every atom is a controller atom, none is registered in `EXCLUSIVE_ATOMS`, and none is a module `CI_PLACEMENT` moves out of the shard matrix.
- **D3. A group is placed as one unit, bounded.** `build_plan` forms, for the atoms of a group that the selection holds, one unit whose estimate is the sum, and assigns units instead of atoms; the shard count is still computed from the individual atoms (the count formula is unchanged) and the unit is expanded back to its atoms, in canonical order, on its shard. A unit carries the canonical index of its first member for the tie-break of `assign_atoms`/`assign_lanes`, which otherwise run unchanged on the unit estimates. The bound: walking the group's atoms by `(-estimate, canonical index)`, an atom joins the unit when the unit's sum stays at most half the mean shard load (`total / count`, integer milliseconds), and an atom that does not fit is skipped and the walk goes on, so smaller atoms still join; the atoms left out are placed individually. A group whose members that fit number fewer than two forms no unit. So one group can never take more than half of an even shard, and an oversized group still keeps together as many of its members as the bound allows. The bound is a named constant, `AFFINITY_MAX_SHARE = 0.5`.
  **No shard is left empty.** `shard_count` caps the count at the number of atoms, and `--shards N` or a raised `--min-shards` can set it to that cap, so merging atoms can leave fewer units than shards (three atoms of 10 s, 1 s and 1 s, the last two in one group, under `--shards 3`: two units, three shards). The rule: after the units are formed, while there are fewer units than the count, the unit with the smallest estimate (ties by the group's name) is split back into its atoms, until there are at least as many units as shards; with every unit split the units are the atoms, which the cap already covers. So every shard count the formula, `--shards` or `--min-shards` can give yields a plan `validate_plan` accepts, the count is the formula's in every case, and affinity is given up only where the count leaves no other choice.
- **D4. The table is advisory.** A member that no longer names an atom is ignored by the planner (a renamed or deleted class never refuses a plan or a run); a test, `tests.test_test_shards`, fails on a stale or duplicated member, an atom in two groups, a group of one, a conformance atom, an atom registered in `EXCLUSIVE_ATOMS` (the registry is empty today) and a module placed out of the shard matrix, so the table is kept honest in CI and not at plan time. No new plan key and no schema change: placement is the only thing that moves, and `validate_plan` is unchanged.
- **D5. Both profiles use it.** The local profile has up to eight shards and the same per-process snapshots, so the same rule applies with the same bound.
- **D6. The saving is counted, not assumed.** Before and after, `built` per row summed over the shard processes of one run over the modules that reach a row, with `WFC_SNAPSHOT_REPORT` set and a CI-profile plan: the item page names the exact commands (`python3 tools/run_tests.py plan --profile ci --ci-placement <modules>` to write the plan, `exec-shard` per shard to run it, the subcommand and option spellings taken from `python3 tools/run_tests.py --help` at implementation time, the same ones before and after), so both figures are reproducible; and the planner's own largest and smallest shard estimates go on the item page with CI2c's caveat: local figures, not a runner, one run each.

## Open decisions

This repository has no `docs/TECHNICAL_DECISIONS.md`; no decision-record "Open decision" row is touched. No owner decision is open: the share bound (D3) is a constant in one place, and the item page records what it gave.

## Checkpoints

<!-- CP1 -->
### CP1: record which tests reach a snapshot row

Implement the measurement half of D1. `tests/fixtures.py` keeps, per key, the set of test ids that reached it (`note_snapshot_user(key, test_id)`; the report line gains a `users` object from a key label to the sorted test ids, the label being the row name and a short content digest of the whole key so two inputs of one row stay apart); `Lifecycle._reach` calls it with `self.test.id()` on the path that restores or keeps a snapshot, and not under the scenario kit (`stub_dir` set), which never shares one. Nothing reads it: no decision depends on it, it is off unless `WFC_SNAPSHOT_REPORT` is set, and `snapshot_counts()` is unchanged. `tests/test_shared_snapshot.py` adds cases to `CountersTest`: a test id is recorded for the key it reached, two lives with different inputs of one row stay apart, a restore and a drive both record, a kit drive records nothing, the label is the same in two processes, and the report line carries `users` and no other key changes.

**Files:** `tests/fixtures.py`, `tests/test_protocol_lifecycle.py`, `tests/test_shared_snapshot.py`

**Tests:** `tests.test_shared_snapshot`, `tests.test_protocol_lifecycle`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: place a group as one bounded unit

Implement D3, D4 and D5 in `tools/test_shards.py` with an empty `SNAPSHOT_AFFINITY`: the constant, `AFFINITY_MAX_SHARE`, a function that turns the selected atoms and the table into placement units (the bounded unit, the leftover atoms alone, the split-back that keeps every shard filled), `build_plan` assigning units through `assign_lanes`/`assign_atoms` and expanding them, and the module docstring's planning paragraph. `build_plan` takes the table as a keyword (default `SNAPSHOT_AFFINITY`), as it takes `exclusive`. `tests/test_test_shards.py` adds cases on a synthetic inventory: a group lands on one shard; the bound keeps the largest members and places the rest alone; a group of one selected atom and a stale member are ignored; a selection that holds part of a group places only that part; the exclusive atoms are untouched; an empty table gives exactly today's plan (digest equal); the plan is deterministic and `validate_plan` accepts it; the shard count equals the count without the table, in every case above; both profiles apply it; the unit carries its first member's canonical index for the tie-break; a member too large for the bound is skipped and a smaller one still joins; a pinned `shards` and a raised `min_shards` equal to the number of selected atoms, with a group that would otherwise merge (the three-atom case of D3), give a valid plan with no empty shard and the count of the formula; the split-back goes smallest group first and ties by name.

**Files:** `tools/test_shards.py`, `tests/test_test_shards.py`

**Tests:** `tests.test_test_shards`, `tests.test_run_tests`, `tests.test_ci_workflows`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP2 -->

<!-- CP3 -->
### CP3: measure and declare the table

Run the measurement of CP1 over the modules that reach a row (`tests.test_protocol_lifecycle`, `tests.test_protocol_job`, `tests.test_functional_codex_route`, `tests.test_review_job` and the modules that subclass its case), once per key label map the test ids to atoms through the inventory, take the groups by D2 (a group is a key, and a class that reaches several keys goes to the key that most of its test ids reached, ties by the lower label), and commit `SNAPSHOT_AFFINITY` in `tools/test_shards.py`. Take the before and after `built` counts of D6 (the same command with the table emptied and with it filled) and keep them for the item page. `tests/test_test_shards.py` adds the table's guard (D4's list: every member names an atom, no duplicate, no atom in two groups, no group under two, no conformance atom, no exclusive atom, no module placed out of the shard matrix) and a case that the committed table moves a CI-profile plan from the committed timings and keeps its digest stable across two runs. `tools/test_timings.json` is not edited.

**Files:** `tools/test_shards.py`, `tests/test_test_shards.py`

**Tests:** `tests.test_test_shards`, `tests.test_run_tests`, `tests.test_ci_workflows`, `tests.test_shared_snapshot`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP3 -->

<!-- CP4 -->
### CP4: the pages and the item records

State the mechanism on `docs/guide/development.md` (next to "Shared lifecycle snapshots" and the planning paragraph of the test runner: what the table is, the bound and why, that it is advisory, how to measure and refresh it with `WFC_SNAPSHOT_REPORT`, and what a new snapshot-reaching class does). Record the item on `docs/items/workflow-controller-ci2g.md` (status, the plan link, the table's groups and sizes, the D6 before and after counts and shard estimates with the local-versus-runner caveat and the measurement commands) and `docs/items/workflow-controller-ci2.md` (the slice marked implemented). `docs/backlog.yaml` and `docs/ROADMAP.md` are unchanged: the item stays `next` until acceptance.

**Files:** `docs/guide/development.md`, `docs/items/workflow-controller-ci2g.md`, `docs/items/workflow-controller-ci2.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-ci2g`.
<!-- /CP4 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | A measurement run names the test classes that reach each snapshot key, without changing any decision or the counts | CP1 |
| R2 | The planner places a declared group of classes on one shard within a share bound, leaves the shard count (pinned and raised counts included, no shard empty), the exclusive atoms and the plan's shape unchanged, and an empty table gives today's plan | CP2 |
| R3 | The committed table is measured, guarded against stale, duplicate and out-of-matrix members, and shown to move the plan without moving its determinism | CP3 |
| R4 | The pages and item records state the mechanism, the bound, the measured counts and the local-versus-runner caveat | CP4 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Record which tests reach a snapshot row | - | 1 | 1 |
| CP2 | Place a group as one bounded unit | CP1 | 2 | 1 |
| CP3 | Measure and declare the table | CP2 | 2 | 1 |
| CP4 | The pages and the item records | CP3 | 1 | 1 |
