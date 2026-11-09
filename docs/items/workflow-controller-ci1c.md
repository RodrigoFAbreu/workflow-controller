# CI1c: measure parallel test workers inside a shard

Item id: `workflow-controller-ci1c`.

An investigation item first. Plan approved; the measurements (CP1) are recorded under [Measurements](#measurements-cp1) below. Measure before changing anything, and adopt only what the measurements support. This item needs no other item (it is best measured after CI1b). Complexity: medium. The planning rules apply: at most one new mechanism, releasable alone. Target for the three CI1 items together: PR CI under 20 minutes ([#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [CI time budget](../planning-rules.md#next-in-order-and-the-sizing-rules)).

## Scope

One mechanism at a time, each measured in a scratch clone (`python3 tools/run_tests.py plan` and `exec-shard`, pinned with `taskset -c 0-3` to match a 4-vCPU hosted runner):

1. Parallel workers for the unittest lane of a shard: shard wall at 1, 2 and 3 workers, with separate `TMPDIR` and `HOME` per worker, CPU use from `/usr/bin/time -v`, and the flake rate over repeated runs (shared `/tmp`, zombie reaping).
2. Candidate: test selection by changed paths, replayed over the last 20 pull request diffs; never skip a test that failed in the full run; main always runs all.
3. Candidate: a larger runner. This needs an owner and billing decision and is not taken without it.

## Numbers (profile in [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317))

Setup is about 10 s of a 20 minute shard, so all the time is the test run. The unittest atoms are subprocess and git heavy, so the 4 vCPUs may be idle about half the time; a plausible gain is 20 to 35 percent per shard if so. The same shard varies from 12.7 to 26.7 minutes between runs, so runner noise matters.

## Out of scope

The docs-only skip (CI1a); splitting atoms and refreshing timings (CI1b); adopting any candidate the measurements do not support.

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Measurements (CP1)

Scratch clone of base commit `065ca1e` (outside the repository), plan `--profile ci` with six shards, `exec-shard --results-dir results` run directly, as CI runs it. Machine: AMD Ryzen 7 9800X3D (16 threads). CPU pinning: a 4-vCPU hosted runner is two cores plus their SMT siblings; here `/sys/devices/system/cpu/cpu0/topology/thread_siblings_list` is `0,8` and cpu1's is `1,9`, so every run used `taskset -c 0,1,8,9`. CPU seconds are `resource.getrusage(RUSAGE_CHILDREN)` user plus system of the run. Workers: an experimental `os.fork` patch to `execute_shard`, in the scratch clone only (atoms divided by the planner's `assign_atoms`, each worker with its own `TMPDIR` and `XDG_STATE_HOME`, `HOME` inherited); nothing is committed. The conformance lane is not parallelised. Runs were interleaved (1, 2, 3 workers; later 1, 2).

**Shards by D2's rule.** Shard A is shard 0 (conformance lane 1120 s, the longest; controller lane 1251 s). The other five shards have controller lanes of about 1250-1251 s (a tie in the rounded profile); shard 4 was taken as the longest by the plan's unrounded estimates (1438.859 s against 1438.858 s). Floor from D2: shard 0 cannot finish below about 1120 x 1.15 = 1288 s at the planned scale.

**Wall seconds (median, range) and CPU.** `n` is the number of runs. CPU change is against the 1-worker median of the same shard.

| shard | workers | n | wall median | wall range | CPU s (median) | CPU change | CPU % of one core |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 1 | 9 | 712.3 | 701.6-1152.0 | 448.0 | - | 63 |
| 0 | 2 | 9 | 514.0 | 507.5-523.3 | 539.3 | +20.4% | 105 |
| 0 | 3 | 3 | 530.8 | 522.7-534.4 | 597.8 | +33.4% | 113 |
| 4 | 1 | 9 | 495.7 | 489.6-683.5 | 432.9 | - | 88 |
| 4 | 2 | 9 | 373.9 | 371.7-380.7 | 498.7 | +15.2% | 134 |
| 4 | 3 | 3 | 311.3 | 309.8-316.2 | 626.0 | +44.6% | 201 |
| 1 | 1 | 3 | 501.9 | 501.9-514.2 | 472.4 | - | 94 |
| 1 | 2 | 3 | 346.3 | 346.0-351.2 | 626.8 | +32.7% | 181 |
| 2 | 1 | 3 | 516.7 | 516.6-521.9 | 445.5 | - | 86 |
| 2 | 2 | 3 | 391.8 | 391.4-395.3 | 542.0 | +21.7% | 138 |
| 3 | 1 | 3 | 577.4 | 577.3-581.9 | 464.1 | - | 80 |
| 3 | 2 | 3 | 372.8 | 371.3-373.1 | 521.4 | +12.3% | 140 |
| 5 | 1 | 3 | 511.5 | 511.0-513.1 | 474.7 | - | 93 |
| 5 | 2 | 3 | 275.2 | 275.0-275.8 | 499.3 | +5.2% | 181 |

Wall change of 2 workers against 1: shard 0 -27.8%, shard 4 -24.6%, shard 1 -31.0%, shard 2 -24.2%, shard 3 -35.4%, shard 5 -46.2%. The six-shard maximum falls from 712.3 s to 514.0 s (shard 0, bounded by its conformance lane). 3 workers: shard 0 is no faster than 2 (530.8 against 514.0 s); shard 4 gains a further 62 s.

**Absolute scale.** These seconds are from a fast desktop pinned to four threads; the planner's hosted-runner estimate is 1439 s per shard, so only the ratios transfer to CI, not the seconds.

**Reliability (D3).** 66 shard runs are kept (the table's `n` summed). None of the kept runs with workers failed and none leaked a process: every N = 2 and N = 3 run exited 0. Two earlier N = 2 runs, the first of shard 0 and of shard 4, were discarded and run again: they failed 20 and 16 tests because the scratch patch read its worker count from an environment variable that the tests' own nested `execute_shard` calls inherited, so those calls tried to fork workers and read `tools/test_timings.json` from a fake repository root (`FileNotFoundError`). The patch was changed to remove the variable before the tests run, the two runs were repeated, and every later worker run passed. That was a fault of the measuring patch, not of running tests in parallel, and D3's exception for a failure fixed by isolating a shared name covers it; the discarded records are kept in the scratch directory. The one failing kept run, `s4-n1-r3` (1 worker), failed `tests.test_worker.LifecycleBracketSupervisionTest.test_an_irregular_bracket_matches_nothing_and_the_wakeup_becomes_overdue`, a timing-sensitive test that failed in the baseline, not a worker effect. Two 1-worker runs were slow outliers on a busy machine (`s0-n1-r9` 1152 s at 37% CPU, and `s4-n1-r6` 683.5 s at 62%); medians are reported, not best runs. The machine-wide zombie count was 0 to 5 before and after every run except 31 before the very first one, at every N. Leftover entries in the per-run `TMPDIR` (which include the scratch patch's worker directories, not removed by it) were 4 to 25 at 2 workers against 4 to 163 at 1; the one shard with more at 2 workers, shard 2 (25 against 21), differs by exactly its four worker directories. The three known `fake_codex_contract` failures did not appear in these shards.

**Verdicts by D4's rule, applied as written.**

- 3 workers: fails CPU (shard 0 +33.4%, shard 4 +44.6%, over 25%) and gains nothing on shard 0. Not extended.
- 2 workers: (a) speed passes on A and B (-27.8%, -24.6%); (b) no shard slower than its 1-worker median, the maximum falls; (c) no D3 veto; (d) CPU: **fails on shard 1** (+32.7%, over 25%); the other five shards pass (+5.2% to +21.7%). Read literally ("each measured shard"), 2 workers **fails the CPU limit** and CP2 commits no code. If (d) was meant for shards A and B only, 2 workers would pass. The rule was fixed before the numbers, so this page records the literal verdict and leaves the reading to the reviewer and the owner rather than choosing the one that adopts the option.

## The other two candidates (D5)

- Test selection by changed paths: not built. It would replay the last 20 pull request diffs, needs pull request history through the git-host adapter, and is a second mechanism. It is the next item if workers do not reach the target. Its one rule: never skip a test that failed in the full run; `main` always runs all.
- A larger runner: the owner's billing decision. The measured gain that would justify it: the shard floor, since the conformance lane (about 1120 s of shard 0) does not use workers and bounds the shard at about 21.5 minutes at planned scale.

Reproduction: `python3 tools/run_tests.py plan --profile ci --ci-placement`, then `taskset -c 0,1,8,9 python3 tools/run_tests.py exec-shard --plan <plan.json> --shard <i> --results-dir results --run-attempt 1` with the experimental patch's worker count set in the environment.

## CP2 (not needed)

No setting passed D4 as written (see the verdicts above), so the option is not written and CP2 changes no source file. Reading (d) as limited to shards A and B would let two workers pass; that reading is for the reviewer and the owner, and adopting it needs a plan amendment that reopens CP2.

## CP3 (the negative result)

No code or workflow file changed. The guides ([How a shard runs](../guide/ci-and-releases.md#how-a-shard-runs) and the
development guide's shards entry) say in one paragraph that the measurements did not support workers and point here.
D8's pull request comparison does not apply, since nothing was adopted: this pull request changes documentation and the
item records only. The D5 decisions stand as written above: no test selection is built, and a larger runner waits for the
owner. The reading of D4(d) is open: if it applies to shards A and B only, two workers pass and a plan amendment reopens
CP2 and CP3 with the option written.
