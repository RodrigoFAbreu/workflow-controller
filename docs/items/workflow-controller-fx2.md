# FX2: fix the flaky functional-sandbox test

Item id: `workflow-controller-fx2`.

Status: implementing (plan revision 3 approved, [plan](../ai-workflow/CONTROLLER_FX2_PLAN.md); CP1 done, CP2 this record). Owner decision 2026-11-19. Priority: high. Complexity: low. Issue: [#206](https://github.com/RodrigoFAbreu/workflow-controller/issues/206).

## Why

`tests.test_functional_sandbox.RunTest.test_restored_source_and_checklist_edits_are_refused_at_the_first_write_and_never_differ` fails intermittently on CI shards whose runs complete. The assertion reads `Lists differ: ['src/app.py'] != []` or `['docs/ACTIVE_MILESTONE.md'] != []`. Instances: PRs #205, #276, #362 (run 43746344398) and #381 (run 44512268006), and earlier runs 41056815867 and 41736173066. It hits roughly one code pull request in three, and each hit costs an hour or more of CI plus a manual rerun.

The root cause was reproduced outside the test. The watcher thread starts before `launch()` builds the sandbox. `git checkout` creates each file empty and only then writes its contents, so a poll in that gap reads `b''`, which differs from the original and is recorded as a write the sandbox failed to refuse. The sandbox is not at fault.

## Scope

One mechanism, in the test only (`tests/test_functional_sandbox.py`, the test at about line 713 and its `poll()` at about lines 721-729): count a mismatch for a path only after the path has been read once equal to the original, meaning the checkout finished writing it. Keep an `armed` set of those paths. After the existing `assertEqual(seen, [])`, add `self.assertEqual(armed, set(paths))`, so the test still proves the watcher saw both files before the refused restore actions ran. A partial read is a strict prefix of the file and can never equal the original.

## Cause and fix

The watcher thread starts before `launch()` builds the clone, and `git checkout` creates each file empty before it writes the contents. A poll between the two reads `b''`, which differs from the original, and was recorded as an unrefused write. The sandbox was correct.

The fix is in the test only (`tests/test_functional_sandbox.py`). `poll()` keeps an `armed` set: a read equal to the original arms the path, and a differing read counts only for an armed path. The watcher sets `both_armed` when both paths are armed; the test waits on it (2 s) after `launch()` returns, then asserts `seen == []` and `armed == set(paths)`, so a watcher that never saw a finished file fails instead of passing vacuously. No `controller/` module changed.

## Measurement

Command: `python3 tools/run_tests.py tests.test_functional_sandbox.RunTest.test_restored_source_and_checklist_edits_are_refused_at_the_first_write_and_never_differ`, looped per copy, copies started together. A run fails when `seen == []` or `armed == set(paths)` fails. Before = the test at the base commit; after = the fix.

| setting | copies x runs per copy | before | after |
| --- | --- | --- | --- |
| pinned, each copy under `taskset -c 0,1` | 3 x 67 (201) | 0 failures | 0 failures |
| unpinned | 3 x 67 (201) | 0 failures | 0 failures |
| `taskset -c 0` (load raised) | 8 x 25 (200) | 0 failures | not run |
| `taskset -c 0` (chosen setting) | 16 x 13 (208) | 1 failure | 0 failures |

The first two settings showed no failure before the change, so the load was raised as the plan's D3 directs. The chosen setting, 16 copies on one CPU, reproduced the race once in 208 before-runs and none in 208 after-runs. One before-failure is a small sample: the count shows the race is reachable and the fix did not fail under the same load, and R1 also rests on the reasoning above (a partial read is a strict prefix of the original and never equals it).

The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
