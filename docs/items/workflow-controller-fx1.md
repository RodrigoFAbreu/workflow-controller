# FX1: fix the timing flakes (OrphanWatchdogTest readiness waits and the Ctrl-C test under load)

Item id: `workflow-controller-fx1`.

Not yet planned. **Owner priority (request 2026-11-13): this item is first in the queue after the current item.** Make two timing-sensitive tests wait for a real readiness signal instead of reading too early. Sources: [#246](https://github.com/RodrigoFAbreu/workflow-controller/issues/246) and [#342](https://github.com/RodrigoFAbreu/workflow-controller/issues/342). Complexity: low.

## Scope

One mechanism: a bounded wait for readiness in the tests, as in the other readiness-signal fixes (`on_spawn` flush, environ empty mid-exec).

- **`OrphanWatchdogTest` (#246).** The stub's pid file is read between its creation and its write (`ValueError: invalid literal for int()`), and `test_the_fakes_own_subprocess_sleepers_are_bounded` was seen failing on 2026-11-13. Wait with a bounded poll of `/proc/<pid>/cmdline` until `argv[0]` is the expected program (and the pid file is non-empty) before the test acts on the process.
- **The Ctrl-C test under load (#342).** `ImplGuardedRunTest.test_a_kill_in_the_middle_of_a_bundle_rewrite_is_run_again_by_resume` fails with `KeyboardInterrupt not raised` under full-suite load. Rule out first the known cause that a background shell ignores SIGINT (see the note on a backgrounded suite), then catch the next failure's diagnostic (the assertion message records the call count, status and regeneration fields) and fix the test or the code it exposes. A loaded local run pinned with `taskset -c 0,1` is the usual repro.

## Out of scope

Other flakes, test sharding and CI time (see CI2), and any change to the Controller's runtime code unless #342's cause is found there.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
