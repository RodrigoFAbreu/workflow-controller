# CI1c: measure parallel test workers inside a shard

Item id: `workflow-controller-ci1c`.

An investigation item first. Not yet planned. Measure before changing anything, and adopt only what the measurements support. This item needs no other item (it is best measured after CI1b). Complexity: medium. The planning rules apply: at most one new mechanism, releasable alone. Target for the three CI1 items together: PR CI under 20 minutes ([#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [CI time budget](../planning-rules.md#next-in-order-and-the-sizing-rules)).

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
