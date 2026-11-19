# FX2: fix the flaky functional-sandbox test

Item id: `workflow-controller-fx2`.

Not yet planned. Owner decision 2026-11-19. Priority: high. Complexity: low. Issue: [#206](https://github.com/RodrigoFAbreu/workflow-controller/issues/206).

## Why

`tests.test_functional_sandbox.RunTest.test_restored_source_and_checklist_edits_are_refused_at_the_first_write_and_never_differ` fails intermittently on CI shards whose runs complete. The assertion reads `Lists differ: ['src/app.py'] != []` or `['docs/ACTIVE_MILESTONE.md'] != []`. Instances: PRs #205, #276, #362 (run 43746344398) and #381 (run 44512268006), and earlier runs 41056815867 and 41736173066. It hits roughly one code pull request in three, and each hit costs an hour or more of CI plus a manual rerun.

The root cause was reproduced outside the test. The watcher thread starts before `launch()` builds the sandbox. `git checkout` creates each file empty and only then writes its contents, so a poll in that gap reads `b''`, which differs from the original and is recorded as a write the sandbox failed to refuse. The sandbox is not at fault.

## Scope

One mechanism, in the test only (`tests/test_functional_sandbox.py`, the test at about line 713 and its `poll()` at about lines 721-729): count a mismatch for a path only after the path has been read once equal to the original, meaning the checkout finished writing it. Keep an `armed` set of those paths. After the existing `assertEqual(seen, [])`, add `self.assertEqual(armed, set(paths))`, so the test still proves the watcher saw both files before the refused restore actions ran. A partial read is a strict prefix of the file and can never equal the original.

The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
