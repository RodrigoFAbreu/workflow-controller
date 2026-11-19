# FX2: fix the flaky functional-sandbox test (Revision 3)

Work item: `workflow-controller-fx2`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `655f9eeefd3cac43f46105706da31395176fd333` ("docs: add FX2 and UF1 before CI2g (#383)"), the tip of `main`, passed explicitly as `/milestone-plan 655f9eeefd3cac43f46105706da31395176fd333`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-fx2` (`docs/backlog.yaml` order 101, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-fx2.md`. It has no dependencies.
Released baseline: the latest tag. The change edits one test and pages and no `controller/` module, so the squash commit is a `ci:` and publishes no release.
Pull request title: `ci: stop a half-written checkout failing the functional-sandbox test (FX2)`

## Goal

`tests.test_functional_sandbox.RunTest.test_restored_source_and_checklist_edits_are_refused_at_the_first_write_and_never_differ` fails on roughly one code pull request in three ([#206](https://github.com/RodrigoFAbreu/workflow-controller/issues/206)). Each failure costs an hour or more of CI and a manual rerun.

The cause is in the test, not the sandbox. Its watcher thread starts before `launch()` builds the clone. `git checkout` creates each file empty and only then writes its contents, so a poll between the two reads `b''`, which differs from the original and is recorded as a write the sandbox failed to refuse. The fix is one mechanism: **a mismatch counts only for a path the watcher has already read equal to its original.**

## What exists

- The test (`tests/test_functional_sandbox.py`, about lines 713-742) polls `clone/src/app.py` and `clone/docs/ACTIVE_MILESTONE.md` every 2 ms from a daemon thread and appends the path to `seen` on any read that differs from the original bytes. It asserts `seen == []`.
- A partial read of a file being written is a strict prefix of the original (empty at first) and so never equals it. A read equal to the original proves the checkout finished that file; any later difference is a real write.

## Decisions

- **D1. Arm a path on its first equal read.** `poll()` keeps an `armed` set. A read equal to the original adds the path to `armed`. A read that differs is appended to `seen` only when the path is already in `armed`. An unreadable path (`OSError`) changes nothing, as today.
- **D2. The test still proves the running watcher watched.** The watcher sets a `threading.Event` (`both_armed`) the moment `armed == set(paths)`. After `launch()` returns, and before `stop.set()`, the test calls `both_armed.wait(2)`; the clone is still there, because `launch_functional` removes a clone only on an exception and `settle_functional()` removes it otherwise (`controller/review.py`, the `finally` block and `settle_functional`). Then, after the existing `assertEqual(seen, [])`, the test asserts `armed == set(paths)`. Only reads made by the running watcher thread arm a path: nothing reads a file after the watcher stops, so a run in which the watcher saw no completed file fails (`armed` stays short) instead of passing vacuously. The wait gives the live watcher the poll it may not have had during a fast `launch()`; it does not replace the watcher.
- **D3. The fix is measured, not assumed.** CP1 records the test run 200 times before the change and 200 times after, through the runner (the test sends signals, so a bare `python3 -m unittest` is not allowed: `CLAUDE.md`, "Running tests"). The command is `python3 tools/run_tests.py tests.test_functional_sandbox.RunTest.test_restored_source_and_checklist_edits_are_refused_at_the_first_write_and_never_differ` (`--plan-only` shows 1 test in 1 shard). Two settings, each with 3 copies started together, each copy looping until its share of the 200 runs is done: **pinned**, every copy prefixed `taskset -c 0,1`, and **unpinned**, the same loop without `taskset`. The after-runs use the same settings as the before-runs, and a run counts as a failure when either assertion fails (`seen == []` or `armed == set(paths)`).
  - If the before-runs show no failure, raise the load (more parallel copies, then `taskset -c 0`) until at least one failure appears, and use that setting for both counts. If none appears even then, CP2 records that the measurement could not show the race, and R1 rests on the reasoning in "What exists".
  - CP2 records the setting (the exact command, the pinning, the number of parallel copies and the runs per copy) next to each count, before and after.
  - There is no after-the-fact read fallback: an `armed` failure in the after-runs is a failure of the fix, reported with its count, and is resolved by lengthening the wait in D2 or by a plan amendment, never by arming a path from a read made after the watcher stopped.
- **D4. Nothing outside the test changes.** No `controller/` module, no helper in `tests/fixtures.py`, no other test. The sandbox's behaviour is correct and stays untouched.

## Open decisions

This repository has no `docs/TECHNICAL_DECISIONS.md`; no decision-record "Open decision" row is touched, and no owner decision is open.

## Checkpoints

<!-- CP1 -->
### CP1: arm the watcher before it counts a difference

Implement D1 and D2 in the one test (D2's `both_armed` wait included), and run the D3 measurement before and after.

**Files:** `tests/test_functional_sandbox.py`

**Tests:** `tests.test_functional_sandbox`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: the item record

Record the item on `docs/items/workflow-controller-fx2.md` (status, the plan link, the D3 before and after counts, and the commands and settings that produced them). `docs/backlog.yaml` and `docs/ROADMAP.md` are unchanged: the item stays `next` until acceptance.

**Files:** `docs/items/workflow-controller-fx2.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-fx2`.
<!-- /CP2 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | A half-written file in the clone is never recorded as an unrefused write, and the test still proves the watcher saw both files finished | CP1 |
| R2 | The item page records the cause, the fix and the before and after measurement | CP2 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Arm the watcher before it counts a difference | - | 1 | 1 |
| CP2 | The item record | CP1 | 1 | 1 |
