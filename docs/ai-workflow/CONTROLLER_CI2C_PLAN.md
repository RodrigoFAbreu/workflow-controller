# CI2c: share fixture snapshots across the tests that reach the same row (Revision 2)

Work item: `workflow-controller-ci2c`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `04ad54507224072eb375d30d31fd5adb2358b056` ("ci: run each test once on a pull request, the conformance suites and the functional flows (CI2b) (#375)"), the tip of `main`, passed explicitly as `/milestone-plan 04ad54507224072eb375d30d31fd5adb2358b056`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ci2c` (`docs/backlog.yaml` order 97, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ci2c.md`. It depends on `workflow-controller-ci2`, which is accepted.
Released baseline: the latest tag. The change edits tests, test helpers and pages only, so the squash commit is a `ci:` and publishes no release (`ci` maps to `none` in the committed policy).
Pull request title: `ci: share fixture snapshots across the tests that reach the same row (CI2c)`

## Goal

Make the slow lifecycle tests build fewer repositories, with one mechanism: the shared lifecycle snapshot (`Lifecycle.reach` with `fixtures.take_snapshot` and `fixtures.restore_snapshot`, `docs/guide/development.md`, "Shared lifecycle snapshots"). The item page lists five scope items (5, 6, 8, 9, 10 of the owner's decision of 2026-11-16 on the CI2 page) and says it may need splitting again at planning. By the sizing rule (one new mechanism per releasable item) this item takes the two that are the snapshot mechanism and moves the rest out:

| scope item | where it goes |
| --- | --- |
| 5. fix the snapshot-reuse misses | this item (CP1, CP2) |
| 6. snapshot `_ReviewCase.setUp` | this item (CP3) |
| 8. measure, then maybe reuse bytecode for the vendored Workflow interpreters | new item `workflow-controller-ci2f`, "process starts" (touches the production isolation path, `-B` in `controller/workflow_contract.py`; one reviewer question of its own) |
| 9. drop the extra `next_action` per test step and cache `inspect()` | `workflow-controller-ci2f` |
| 10. put tests that share a snapshot key on the same shard | new item `workflow-controller-ci2g`, "shard affinity" (a planner change in `tools/test_shards.py`; it only pays once the snapshot keys are the trimmed ones of CP2, and the planner is a different mechanism from a fixture) |

CP4 records the split in `docs/backlog.yaml` and on the pages. The expected saving is read off counters and a timed run, not promised: CP1 adds the counters, CP2 and CP3 record the figures they change, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target) stays open.

## What exists

- **The snapshot.** `Lifecycle.reach(name, drive)` in `tests/test_protocol_lifecycle.py` builds the key `(name, snapshot_inputs())`. The first life to ask drives for real and `fixtures.take_snapshot` copies its tree and saved attributes (`SNAPSHOT_ATTRS`); every later life with the same key calls `fixtures.restore_snapshot`, which makes a copy at the path the tree was built at (a bundle's `MANIFEST.md` records the worktree path and its `bundle_id` hashes it, so a tree cannot move) and returns the saved state. `restore_snapshot` returns `None`, and the life drives, when the key has no snapshot, when the path is in use or when the copy fails. Under the scenario kit (`stub_dir` on the test object) `reach` always drives.
- **Who uses it.** `GateLifecycle` (rows `to_plan_gate`, `to_acceptance`), `LoopLifecycle` (`to_refused_acceptance` and `to_acceptance`) in `tests/test_protocol_lifecycle.py`, the `to_38e` row in `tests/test_protocol_job.py` and `CodexLifecycle` in `tests/test_functional_codex_route.py`. `tests/test_review_job.py`'s `_ReviewCase.setUp` builds a base `Lifecycle` and drives it to `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` for every test (84 tests load in `tests.test_review_job` at the base commit, 80 of them `_ReviewCase` subclasses) without `reach`.
- **Measured on this machine, with a scratch wrapper around `restore_snapshot` (no repository change), `unshare` for the PID namespace:**

| modules | tests | snapshots built | restored | refused, path in use |
| --- | --- | --- | --- | --- |
| `tests.test_protocol_lifecycle` | 23 | 14 (`to_acceptance` 8, `to_refused_acceptance` 5, `to_plan_gate` 1) | 8 | 0 |
| `tests.test_protocol_job` and `tests.test_functional_codex_route`, in one process | 95 | 6 (`to_38e` 6) | 32 | 4 |

  So most rows are built many times because the keys differ, not because the snapshot fails: `to_acceptance` is built eight times in one module. A key part is any `snapshot_inputs()` value, and `GateLifecycle` puts into it the text of the `checks` file and the set `refused`, which only the last steps read (`/satisfy-gate acceptance` reads `checks`; `/satisfy-gate plan` and `implementation` read `refused`). `LoopLifecycle` adds its scripted pull request (`world`), the binding, the acceptance policy and the wait. The four refusals are tests that hold two lives of one row at once (for example `test_a_launch_without_rollouts_gives_the_empty_record` calls `self.codex()` twice): the second copy would have to sit at the first one's path.
- **The review case.** On this machine `Lifecycle(...)` takes 0.5 s and `run_until("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")` 1.2 s; the tree is 3.3 MB and copies in under 0.01 s. The 7 s per test on a CI runner is mostly that drive.
- **The scenario kit.** `tests/scenarios/review.py` builds a base `Lifecycle` through a kit test object, but no scenario calls `reach`, so the "always drives under the kit" branch costs nothing today (checked by grep; CP1's counter proves it in a full run).

## Decisions

- **D1. Measure first, in the repository.** `tests/fixtures.py` counts, per key name, the outcome of every `restore_snapshot` and `take_snapshot` call (`built`, `restored`, `in_use`, `copy_failed`, `unkept`, and the two calls that do nothing, `missing` for a `restore_snapshot` of a key with no snapshot yet, the first life of a row, and `already_kept` for a `take_snapshot` of a key already kept, the drive after an `in_use` refusal, so that `built` counts only a snapshot actually taken) and `Lifecycle.reach` counts the `kit_drove` outcome. `fixtures.snapshot_counts()` returns the counts; when the environment variable `WFC_SNAPSHOT_REPORT` names a file, an `atexit` hook appends one JSON line with them (the process id, the counts). It is read-only reporting: it changes no decision and is off by default. CP1 records the baseline, CP2 and CP3 the figures after.
- **D2. A path in use is inherent, and not fixed.** A tree records its own path, so a second live copy of the same row in one test cannot exist and must drive. The counter makes that visible as `in_use`; the item records the count (4 of 95 tests in the two measured modules, 0 of 23 in the third) and changes nothing. This closes that part of scope item 5 by measurement: the refusal is correct.
- **D3. The scenario kit stays as it is.** `kit_drove` is expected to be 0 in a full run; CP1 records the number it finds. A nonzero number would be a new finding for a later item, never a reason to enable snapshots inside the kit's isolation boundary in this one.
- **D4. A key holds what the drive reads, and nothing else.** `snapshot_inputs()` returns a mapping from a part name to its value (`release`, `report`, `checks`, `refused`, ...), and `reach(name, drive, *, late=())` builds the key from the parts not named in `late`. A `late` part is one the drive for that row never reads. Two rules make that checkable instead of trusted: (a) a late part is read only through `life.late(name)`, which raises `AssertionError` while a `reach` drive that declared it late is running, so a drive that does read it fails loudly in the test that declared it, never silently; (b) `reach` captures the life's own value of each late part when it starts, the drive runs with the part's canonical value, and after the drive or the restore `reach` applies the captured value (`apply_late`: `GateLifecycle` writes the `checks` file; `refused` is a plain attribute the life already holds). Candidates, decided by (a) in CP2 and recorded with the result: `checks` for `to_plan_gate` and `to_acceptance`, and `refused` for `to_plan_gate`. `LoopLifecycle`'s `world` is **not** a candidate and stays in the key (review round 1, I1): its reads do not go through an effect that `life.late` could wrap (the Controller's own query reads it through the forge seam, `LoopLifecycle.step` patching `GhForge` to the stand-in and `PrWorld.query_pull_requests` reading `world_raw(self.path)`, and the effects pass `str(self.world.path)` to `_SATISFY_ACCEPTANCE_WORLD` and `_APPLY_PR_REVIEW_WORLD`), so a guard could not trip on them and two lives with different worlds would silently share a tree. A part that trips (a) stays in the key. Nothing in `controller/` changes.
- **D5. One snapshot for the review case.** `_ReviewCase.setUp` reaches the plan gate through `self.life.reach("to_manual_plan_review", ...)`, builds `reviewer(self.life.td)` and registers the worker reaper **after** the reach (the tree may have been rebased to the snapshot's path), and keeps the rest of `setUp` as it is. The key carries `release` (so the one `2.12.0` class is its own row) and `report`. The clock is part of the snapshot by construction (review round 1, I2): `_ReviewCase.setUp` sets `self.now = time.time() - 600` before the drive, and the drive's usage writes (`usage_now()` in `controller/job.py`) carry that value, so a copy restored later in the shard would otherwise sit at a gap from the current test's `self.now` that grows with machine speed. The review case therefore builds its life as a small `Lifecycle` subclass whose `SNAPSHOT_ATTRS` adds `review_now`, sets `life.review_now = self.now` before `reach`, and after the reach sets `self.now = life.review_now` (the saved value on a restore, the same value on a drive); the patched `usage_now` reads `self.now` through its lambda, so every record age a test compares is the one a fresh build gives, whatever the machine speed. A class that still differs after that (a read of the wall clock through `time.time()` directly rather than `usage_now`) keeps its own drive through a class attribute `SHARE_SNAPSHOT = False` and says why in a comment; the flag is added only for a class that fails, never in advance, and CP3 runs the module a second time under `taskset -c 0,1` with parallel copies to widen the gap, recording both results.
- **D6. Fresh equals copy.** A test in `tests/test_shared_snapshot.py` drives the review-case row fresh and restored and compares what the review tests read: the phase, the launched `tasks`, the sorted file list of the tree relative to its root, the Workflow's `next_action` row, `self.now` and the usage record a review test reads (`usage.load_record`, compared after replacing nothing: its timestamps must be equal to the fresh ones relative to `self.now`). It is the existing "a copy behaves like a fresh build" proof applied to the new row.

## Open decisions

No repository decision-record "Open decision" row is touched (this repository has no `docs/TECHNICAL_DECISIONS.md`). Owner decision, provisional: **O1. The split.** Scope items 8 and 9 and scope item 10 are moved to two new backlog items (`workflow-controller-ci2f`, `workflow-controller-ci2g`) instead of being done here, because each is its own mechanism (the production isolation path; the shard planner). Nothing the owner approved is dropped; the order is the owner's to change in `docs/backlog.yaml`. **Owner: say "keep them in CI2c" before CP4 starts and the plan is amended; otherwise CP4 adds the two items.**

## Checkpoints

<!-- CP1 -->
### CP1: count the snapshot outcomes and record the baseline

Add the counters of D1 to `tests/fixtures.py` (`snapshot_counts()`, the `WFC_SNAPSHOT_REPORT` hook, the per-call outcomes in `take_snapshot` and `restore_snapshot`) and the `kit_drove` outcome to `Lifecycle.reach` in `tests/test_protocol_lifecycle.py`. In `tests/test_shared_snapshot.py` add tests that each outcome is counted once per call (a built row, a restored row, a row refused because its path is in use, a failed copy, a key that could not be kept, a restore with no snapshot yet (`missing`), a take of a key already kept (`already_kept`), a kit drive), that the counts are keyed by the row name, and that the report file gets one JSON line and nothing is written with the variable unset. Run the three lifecycle modules with the report on and write the numbers (built, restored, in use, kit) next to the table above in the item page at CP4; the figures from this checkpoint are the baseline CP2 and CP3 are compared with.

**Files:** `tests/fixtures.py`, `tests/test_protocol_lifecycle.py`, `tests/test_shared_snapshot.py`

**Tests:** `tests.test_shared_snapshot`, `tests.test_protocol_lifecycle`, `tests.test_package_structure`, `tests.test_write_containment`, `tests.test_docs`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: a key holds what the drive reads

Implement D4. `snapshot_inputs()` becomes a mapping in `Lifecycle` and its four subclasses (`GateLifecycle`, `LoopLifecycle`, the `to_38e` class in `tests/test_protocol_job.py`, `CodexLifecycle` in `tests/test_functional_codex_route.py`); `reach(name, drive, *, late=())`, `life.late(name)` and `apply_late` are added; the effects that read `checks` and `refused` go through `life.late`. Declare `late` for each candidate row and keep every part that trips the guard in the key. `tests/test_shared_snapshot.py`'s three uses of `(name, life.snapshot_inputs())` follow (a `snapshot_key` helper), and its tests add: two lives that differ only in a late part share one snapshot and each sees its own value afterwards (the `checks` file, the `refused` set); a drive that reads a late part fails with the guard's message; a part not declared late still separates keys; a key's parts are named. Run the three lifecycle modules with the report on and record the built and restored counts against CP1's baseline.

**Files:** `tests/test_protocol_lifecycle.py`, `tests/test_protocol_job.py`, `tests/test_functional_codex_route.py`, `tests/test_shared_snapshot.py`

**Tests:** `tests.test_shared_snapshot`, `tests.test_protocol_lifecycle`, `tests.test_protocol_job`, `tests.test_functional_codex_route`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP2 -->

<!-- CP3 -->
### CP3: the review case reaches its row once

Implement D5 and D6 in `tests/test_review_job.py` (`_ReviewCase.setUp`, the optional `SHARE_SNAPSHOT` flag) and `tests/test_shared_snapshot.py` (the fresh-equals-copy test). Run `tests.test_review_job` before the edit (as the base commit) and after, with the report on and the elapsed time of each, and once more after under `taskset -c 0,1` with two parallel copies, and record the two results, the built and restored counts for the `to_manual_plan_review` rows and any class given `SHARE_SNAPSHOT = False` with its reason.

**Files:** `tests/test_review_job.py`, `tests/test_shared_snapshot.py`

**Tests:** `tests.test_review_job`, `tests.test_shared_snapshot`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP3 -->

<!-- CP4 -->
### CP4: the pages, the split and the item records

State the mechanism on `docs/guide/development.md` ("Shared lifecycle snapshots": the key holds what the drive reads, the late parts and their guard, the review case, the counters and `WFC_SNAPSHOT_REPORT`, the inherent in-use refusal and the kit). Record the measured figures (the table above, the baseline, after CP2 and after CP3, the elapsed times and the local-versus-runner caveat) in `docs/items/workflow-controller-ci2c.md`, with its status and a link to this plan. Retitle that item in `docs/backlog.yaml` to "CI: fixture snapshot reuse (#317)". Add the two new items there, `workflow-controller-ci2f` ("CI: process starts per lifecycle step (#317)", scope items 8 and 9) and `workflow-controller-ci2g` ("CI: shard affinity for tests that share a snapshot (#317)", scope item 10), `workflow-controller-ci2f` with `depends_on: [workflow-controller-ci2]` (it does not use the snapshot mechanism) and `workflow-controller-ci2g` with `depends_on: [workflow-controller-ci2c]` (it only pays once the snapshot keys are the trimmed ones), after `workflow-controller-ci2e`, with the later `order` values moved up by two; write their pages `docs/items/workflow-controller-ci2f.md` and `docs/items/workflow-controller-ci2g.md` (scope, source, the planning rules link); update the slice table on `docs/items/workflow-controller-ci2.md`; regenerate `docs/ROADMAP.md` with `python3 scripts/workflow_backlog.py generate`.

**Files:** `docs/guide/development.md`, `docs/items/workflow-controller-ci2c.md`, `docs/items/workflow-controller-ci2.md`, `docs/items/workflow-controller-ci2f.md`, `docs/items/workflow-controller-ci2g.md`, `docs/backlog.yaml`, `docs/ROADMAP.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-ci2c`.
<!-- /CP4 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | Every snapshot outcome is counted and reportable, and the baseline, the inherent path-in-use refusal and the kit's zero are recorded | CP1 |
| R2 | A snapshot key holds only what its drive reads, a drive that reads a late part fails loudly, and every life still sees its own late values | CP2 |
| R3 | The review case reaches its plan-gate row once per process and a restored copy matches a fresh build | CP3 |
| R4 | The pages and item records state the mechanism and the measured figures, and the remaining scope items are their own backlog items | CP4 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Count the snapshot outcomes and record the baseline | - | 1 | 1 |
| CP2 | A key holds what the drive reads | CP1 | 2 | 1 |
| CP3 | The review case reaches its row once | CP2 | 1 | 1 |
| CP4 | The pages, the split and the item records | CP3 | 1 | 1 |

