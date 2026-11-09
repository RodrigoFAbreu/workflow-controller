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

Scratch clone of base commit `065ca1e` (outside the repository), plan `--profile ci` with six shards, `exec-shard --results-dir results` run directly, as CI runs it. Machine: AMD Ryzen 7 9800X3D (16 threads). CPU pinning: a 4-vCPU hosted runner is two cores plus their SMT siblings; here `/sys/devices/system/cpu/cpu0/topology/thread_siblings_list` is `0,8` and cpu1's is `1,9`, so every run used `taskset -c 0,1,8,9`. CPU seconds are `resource.getrusage(RUSAGE_CHILDREN)` user plus system of the run. Workers: an experimental `os.fork` patch to `execute_shard`, in the scratch clone only. It divides the atoms with the planner's `assign_atoms` **over the profile's per-atom `seconds` estimates**, gives each worker its own `TMPDIR` and `XDG_STATE_HOME` (`HOME` inherited), and takes the worker count from `CI1C_WORKERS`; nothing is committed. The conformance lane is not parallelised. Runs were interleaved. The patch, the wrapper, the drivers and the set-up are on [the reproduction page](workflow-controller-ci1c-reproduction.md), so any number below can be reproduced.

**Shards by D2's rule.** Shard A is shard 0 (conformance lane 1120 s, the longest; controller lane 1251 s). Shard B is the shard with the longest Controller lane among the others: the planner's lane milliseconds are 1251181 for shards 1, 2 and 3 and 1251182 for shards 4 and 5, and the tie between 4 and 5 goes to the lower index, so shard 4. (The whole-shard estimates are a near-tie of 1438.858-1438.859 s and were not the rule.) Floor from D2: shard 0 cannot finish below about 1120 x 1.15 = 1288 s at the planned scale.

**Wall seconds (median, range) and CPU.** `n` is the number of runs. CPU change and wall change are against the 1-worker median of the same shard.

| shard | workers | n | wall median | wall range | wall change | CPU s (median) | CPU change | CPU % of one core |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 1 | 9 | 712.3 | 701.6-1152.0 | - | 448.0 | - | 63 |
| 0 | 2 | 9 | 516.2 | 499.5-635.4 | -27.5% | 523.2 | +16.8% | 101 |
| 0 | 3 | 3 | 513.4 | 513.3-513.8 | -27.9% | 564.1 | +25.9% | 110 |
| 4 | 1 | 9 | 495.7 | 489.6-683.5 | - | 432.9 | - | 87 |
| 4 | 2 | 9 | 320.5 | 311.9-392.6 | -35.3% | 505.9 | +16.9% | 158 |
| 4 | 3 | 3 | 242.5 | 242.4-243.0 | -51.1% | 604.0 | +39.5% | 249 |
| 1 | 1 | 3 | 501.9 | 501.9-514.2 | - | 472.4 | - | 94 |
| 1 | 2 | 3 | 344.8 | 329.4-392.5 | -31.3% | 645.7 | +36.7% | 187 |
| 2 | 1 | 3 | 516.7 | 516.6-521.9 | - | 445.5 | - | 86 |
| 2 | 2 | 3 | 325.6 | 317.2-386.1 | -37.0% | 526.4 | +18.2% | 162 |
| 3 | 1 | 3 | 577.4 | 577.3-581.9 | - | 464.1 | - | 80 |
| 3 | 2 | 3 | 338.6 | 338.5-416.1 | -41.4% | 514.9 | +10.9% | 152 |
| 5 | 1 | 3 | 511.5 | 511.0-513.1 | - | 474.7 | - | 93 |
| 5 | 2 | 3 | 269.6 | 269.3-361.8 | -47.3% | 501.0 | +5.5% | 186 |

The six-shard maximum falls from 712.3 s (1 worker, shard 0) to 516.2 s (2 workers, shard 0, bounded by its conformance lane). 3 workers: shard 0 is no faster than 2 (513.4 against 516.2 s); shard 4 gains a further 78 s.

**Absolute scale.** These seconds are from a fast desktop pinned to four threads; the planner's hosted-runner estimate is 1439 s per shard, so only the ratios transfer to CI, not the seconds.

**Discarded runs.** Three sets of runs are not in the table, each with its cause:
- The first two 2-worker runs (shards 0 and 4) failed 20 and 16 tests: the patch read its worker count from an environment variable that the tests' own nested `execute_shard` calls inherited, so they forked workers and read `tools/test_timings.json` from a fake repository root (`FileNotFoundError`). The patch now removes the variable before the tests run. This was a fault of the measuring patch, covered by D3's exception for a failure fixed by isolating a shared name.
- **The 36 first 2- and 3-worker runs (all six shards), which split the atoms by count, not by estimate.** The first patch read each atom's profile entry as a number, but every entry of `atoms` in `tools/test_timings.json` is a dictionary (`samples`, `seconds`, `tests`), so every atom got the same 1000 ms estimate and `assign_atoms` divided them by count. The workers were then unbalanced (for example shard 4 at 2 workers: by the profile's estimates the count-based split gave 451.8 s and 799.4 s, where a balanced split gives 625.6 s each), which set the wall by the overloaded worker and changed the CPU overlap. That is not the split D2 and D6 specify, so those runs were discarded when a reviewer found it; they are kept in the scratch directory. The patch now reads `seconds` (shard 4 at 2 workers: 625.6 s each by the estimates, twice the lane's 1251 s; at 3, 417.1 s each) and 2 and 3 workers were measured again under D2's run counts: three interleaved rounds of each on shards 0 and 4, 3 workers ended there (below), 2 workers were extended to 9 runs on shards 0 and 4 and run three times on each of the other four shards. The 1-worker baselines are the original runs, since with one worker the patch changes nothing.

**Reliability (D3).** 66 shard runs are kept (the table's `n` summed). None of the kept runs with workers failed and none leaked a process: every 2- and 3-worker run exited 0. The one failing kept run, `s4-n1-r3` (1 worker), failed `tests.test_worker.LifecycleBracketSupervisionTest.test_an_irregular_bracket_matches_nothing_and_the_wakeup_becomes_overdue`, a timing-sensitive test that failed in the baseline, not a worker effect. Slow runs are in the data and the medians are reported, never the best runs: two 1-worker outliers (`s0-n1-r9` 1152 s at 37% CPU, `s4-n1-r6` 683.5 s at 62%), and 2-worker runs of shard 0 from round 5 (635.4, 594.7, 622.4 and 624.6 s) and of shard 4 (up to 392.6 s) while the machine was busy; the first round of shards 1, 2, 3 and 5 was also slower and used more CPU than the later two, which is why the wall ranges are wide. The machine-wide zombie count after each run was at most 5 at 1 worker and at most 3 at 2 and 3 workers (31 before the very first run). Leftover entries in the per-run `TMPDIR` (which include the patch's worker directories, not removed by it) were 4 to 25 at 2 workers against 4 to 163 at 1; the one shard with more at 2 workers, shard 2 (25 against 21), differs by exactly its four worker directories. The three known `fake_codex_contract` failures did not appear in these shards.

**Verdicts by D4's rule, applied as written.**

- 3 workers: fails (d) after three rounds (shard 0 +25.9%, shard 4 +39.5%, over 25%, in every one of the three rounds on shard 4 and the three on shard 0) and gains nothing on shard 0. Not extended.
- 2 workers: (a) speed passes on A and B (-27.5%, -35.3%), (b) no shard slower than its 1-worker median and the maximum falls (712.3 s to 516.2 s), (c) no D3 veto over 30 runs; (d) CPU: **fails on shard 1** (+36.7% at the median, and +30.4% to +56.2% in each of its three runs); the other five shards pass (+5.5% to +18.2%). Read literally ("each measured shard"), 2 workers **fails the CPU limit** and CP2 commits no code. If (d) was meant for shards A and B only, 2 workers would pass. The rule was fixed before the numbers, so this page records the literal verdict and leaves the reading to the owner rather than choosing the one that adopts the option. The corrected split did not change the verdict; it moved shard 1 from +32.7% to +36.7% and the shard A and B figures to the numbers above.

## The other two candidates (D5)

- Test selection by changed paths: not built. It would replay the last 20 pull request diffs, needs pull request history through the git-host adapter, and is a second mechanism. It is the next item if workers do not reach the target. Its one rule: never skip a test that failed in the full run; `main` always runs all.
- A larger runner: the owner's billing decision. The measured gain that would justify it: the shard floor, since the conformance lane (about 1120 s of shard 0) does not use workers and bounds the shard at about 21.5 minutes at planned scale.

Reproduction: the patch, the `getrusage` wrapper, `CI1C_WORKERS`, the per-run `TMPDIR` and the drivers are on [the reproduction page](workflow-controller-ci1c-reproduction.md).

## CP2 (not needed)

No setting passed D4 as written (see the verdicts above), so the option is not written and CP2 changes no source file. Reading (d) as limited to shards A and B would let two workers pass; that reading is for the reviewer and the owner, and adopting it needs a plan amendment that reopens CP2.

## CP3 (the negative result)

No code or workflow file changed. The guides ([How a shard runs](../guide/ci-and-releases.md#how-a-shard-runs) and the
development guide's shards entry) say in one paragraph that the measurements did not support workers and point here.
D8's pull request comparison does not apply, since nothing was adopted: this pull request changes documentation and the
item records only. The D5 decisions stand as written above: no test selection is built, and a larger runner waits for the
owner. The reading of D4(d) is open: if it applies to shards A and B only, two workers pass and a plan amendment reopens
CP2 and CP3 with the option written.
