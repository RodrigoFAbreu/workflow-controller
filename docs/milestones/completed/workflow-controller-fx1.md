# FX1: wait for readiness in the two timing-sensitive tests, #246 and #342 (Revision 2)

Work item: `workflow-controller-fx1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `a2e1cc1bec60a0d70763f522ac6515d7e931d06c` ("feat: route the milestone-implement-self-review role to Codex in a private clone, landing its commits under a checked state profile and regenerating the implementation bundle from the carried-out files (H4g, #312) (#345)"), the tip of `main`, passed explicitly as `/milestone-plan a2e1cc1bec60a0d70763f522ac6515d7e931d06c`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-fx1` (`docs/backlog.yaml` order 85; owner priority, request 2026-11-13). Its description is `docs/items/workflow-controller-fx1.md`. It depends on nothing.
Released baseline: the latest tag. Only tests change unless #342's cause is found in the Controller's runtime code, so the squash commit is a `fix:` and releases a patch version.
Pull request title: `fix: make OrphanWatchdogTest wait for the stub's readiness and explain the Ctrl-C test's failure under load (FX1, #246, #342)`

## Goal

Two timing flakes stop failing runs. One mechanism: a bounded wait for a real readiness signal in the tests, as in the other readiness fixes (`on_spawn` flush, environ empty mid-exec). Nothing else about the suite, its sharding or its CI time changes (that is CI2).

## What exists

- `OrphanWatchdogTest` (`tests/test_fake_claude_contract.py`) reads a pid file as soon as `Path.exists` is true. The stub or the fake creates the file and then writes it, so a read between the two gets `''` and `int('')` raises (#246; seen again on 2026-11-13 in `test_the_fakes_own_subprocess_sleepers_are_bounded`). Two of its pid files are JSON, not an integer: `_INTERMEDIATE` and `_LAUNCHER` write theirs in place (`open(..., "w")` plus `json.dump`), and `_start` and `test_a_fake_that_does_not_lead_its_group_ends_only_itself` read them with `json.loads`, so the same empty or partial read gives `JSONDecodeError`. The integer files written by `bash_bg` daemons (a shell `echo $! >`) are read by `test_a_daemon_leftover_has_a_finite_life` and `test_a_gated_setsid_descendant_blocked_on_its_fifo_ends`, and `test_a_bounded_controller_ends_at_its_deadline_when_its_starter_is_killed` reads the stub's pid file the same way. The hang-child pid file (`tests/fake_claude.py:1461-1466`) is already written atomically and `subprocess.Popen` returns only after exec, so that read already sees `sleep` and needs nothing. The `/proc/<pid>/cmdline` race is elsewhere: the monitor's sleeper in `test_the_fakes_own_subprocess_sleepers_are_bounded` is found through `_children(found["fake"])` (`/proc/<pid>/task/<pid>/children`), which lists a forked child before it execs, so `argv[0]` can still be `python3` (the #246 comment, PR #343).
- `ImplGuardedRunTest.test_a_kill_in_the_middle_of_a_bundle_rewrite_is_run_again_by_resume` (`tests/test_job_codex_apply.py`) patches `generator_run.workflow_contract.run_in_place` so its first call runs the real generator, tears the bundle and raises `KeyboardInterrupt`. It is meant to be raised by the patch itself, not by a signal, so a background shell that ignores SIGINT (see the note on a backgrounded suite) cannot be its cause. `KeyboardInterrupt not raised` means `self.step()` ended normally: the patched call was never reached (`generator_run.run` ends first in `check_landing`, `check_tree` or `check_copy_sources` with a `GeneratorRefused`), or the real generator inside the patch failed so the patch raised something else. The assertion message already records `(len(calls), seen, status, regeneration, reconciliation_evidence)`, which holds a refusal's status and evidence but not its `regeneration.cause` and `detail` printed on their own.
- #342 reports a pair: the companion `GuardedRunMixin.test_a_kill_during_the_run_is_run_again_by_resume` (`tests/test_job_codex_apply.py:1017`, run for `ImplGuardedRunTest`) patches `run_in_place` with `side_effect=KeyboardInterrupt` under a bare `assertRaises` and has no diagnostic. No real generator runs inside its patch, so it can fail only when `run_in_place` is never reached. Both sightings were in full-suite runs (`tools/run_tests.py` shard 5, then a heavily loaded 13-minute full run), and #342 says a pinned parallel repro of the module did not reproduce it.

## Decisions

- **D1. A pid is read only once it parses.** One test helper polls with `process_fixtures.wait_until` (bounded, 30 s) until the file's content parses, and returns the parsed value: an integer by default, or, for the JSON files of `_INTERMEDIATE` and `_LAUNCHER`, a JSON object holding the expected keys. The writers stay as they are (one mechanism, in the reader; both live in the same file and a reader that waits is also safe against any later writer). No test reads a pid file for its content straight after `exists`: every non-atomic read of a pid file in `OrphanWatchdogTest` goes through the helper.
- **D2. A process is acted on only when it is the expected program.** Where a test takes a pid from `_children(...)` and reads its `/proc/<pid>/cmdline` (`sleep_argument` for the monitor's sleeper), it first waits (bounded) until `argv[0]` is the expected program, so the wait sits where the race is.
- **D3. The Ctrl-C test is fixed from evidence, not from a guess.** The cause is looked for by a loaded repro that includes a full-suite run, with a diagnostic on both tests of the pair. The pair narrows the cause: the companion fails only when `run_in_place` is never reached, so when both fail together the cause is a `GeneratorRefused` before the run (or the step ending before `generator_run.run`), and the "generator failed inside the patch" end can explain the torn test alone. The fix goes where the cause is: the test when its set-up or its patch is racy, the Controller code when a real defect shows. A cause that cannot be reproduced after the bounded repro below is not invented: both tests then fail with a diagnostic that names which end happened (`len(calls) == 0` with the refusal's `regeneration.cause` and `detail`, or the generator's own failure), and the issue stays open with that finding.

## Checkpoints

<!-- CP1 -->
### CP1: OrphanWatchdogTest reads a pid only when it is ready

Add the bounded readiness helper (D1) and the `argv[0]` wait (D2) in `OrphanWatchdogTest`, and use them at every non-atomic pid-file read in that class: `_start` (JSON), `test_a_fake_that_does_not_lead_its_group_ends_only_itself` (JSON), `test_a_daemon_leftover_has_a_finite_life`, `test_a_gated_setsid_descendant_blocked_on_its_fifo_ends`, `test_a_bounded_controller_ends_at_its_deadline_when_its_starter_is_killed`; and at the `_children` sleeper's `argv[0]` in `test_the_fakes_own_subprocess_sleepers_are_bounded`. A grep for `read_text()` of a pid path in the class finds none left outside the helper. Tests: a test of the helper itself that starts a writer which creates the file, pauses, then writes the pid, and shows the helper returns that pid and never an empty read; the same for a JSON writer that writes the object in two parts (the helper returns the complete object, never a `JSONDecodeError`); the helper times out with a message naming the file when nothing is written. A loaded repro: the class run in three copies pinned with `taskset -c 0,1` passes five times in a row.

**Files:** `tests/test_fake_claude_contract.py`
<!-- /CP1 -->

<!-- CP2 -->
### CP2: the Ctrl-C test under load, from evidence

Reproduce `test_a_kill_in_the_middle_of_a_bundle_rewrite_is_run_again_by_resume` and its companion `test_a_kill_during_the_run_is_run_again_by_resume` under load, by two methods: the module run in six parallel copies pinned with `taskset -c 0,1` (each in its own scratch directory, `mktemp`), repeated up to ten times; and a bounded loaded full-suite run (`tools/run_tests.py` replaying shard 5 under parallel load, then one full run under the same load), since both sightings were full-suite runs and the first method is already reported as not reproducing it. Read from the diagnostics which end happened (D3) and whether the pair fails together. Fix the cause where it is. When no failure is caught, give both tests the same self-explaining failure (when the interrupt is not raised, record `len(calls)`, the record's `status`, and the `regeneration` `cause`, `detail` and `reconciliation_evidence` printed explicitly, which the present message holds only inside the record), keep their behaviour, and record in the issue what was tried. If the cause is in the Controller's runtime code, that change and its test land here and the Files line below is extended in the plan's revision, not silently. The pages that describe flake repro (`docs/guide/development.md`) get one line on the finding.

**Files:** `tests/test_job_codex_apply.py`, `docs/guide/development.md`, `docs/items/workflow-controller-fx1.md`
<!-- /CP2 -->

## Open decisions

None of `docs/TECHNICAL_DECISIONS.md`'s "Open decision" rows is touched. The one scope question, whether a Controller change is allowed for #342, is answered by the item: only if the cause is found there.

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | `OrphanWatchdogTest` reads a pid file and a process's argv only once they are ready, with a bounded wait (#246) | CP1 |
| R2 | The Ctrl-C test and its companion fail under load for a cause shown by evidence and fixed where it is, or both are made self-explaining when it cannot be reproduced (#342) | CP2 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | OrphanWatchdogTest reads a pid only when it is ready | - | 2 | 1 |
| CP2 | The Ctrl-C test under load, from evidence | - | 3 | 1 |
