# CI2f: process starts per lifecycle step

Item id: `workflow-controller-ci2f`.

Status: implemented on the milestone branch (four checkpoints); plan: [CONTROLLER_CI2F_PLAN.md](../ai-workflow/CONTROLLER_CI2F_PLAN.md) (revision 2). A `ci:` change that edits tests, test helpers and pages only; it publishes no release. A slice of [CI2](workflow-controller-ci2.md), split out of [CI2c](workflow-controller-ci2c.md) at its planning because it is its own mechanism and touches the production isolation path. Depends on CI2. Sources: scope items 8 and 9 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: a lifecycle step starts fewer processes.

8. Measure first, then maybe reuse bytecode for the vendored Workflow interpreters (3 to 8 min if confirmed, medium-high risk because it touches the production isolation path, `controller/workflow_contract.py:719`, `-B`). One `-X importtime` measurement on a runner decides.
9. Drop the extra `next_action` per test step and cache `inspect()` (about 1 min).

One lifecycle step runs about 10 Python interpreters, each compiling about 30k lines of vendored Workflow code, plus 60 to 90 git calls, about 3 s per step. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## What was done

- **Counting (CP1).** `fixtures.count_process_starts()` counts each process start by kind and restores `Popen`.
- **The decided row (CP2).** `Lifecycle.decided_row` reads the row from the step's own `next-action` decision at the six sites that asked for it before the step. Production queries are unchanged.
- **A cached inspect (CP3).** `fixtures.inspect_unchanged` serves a repeated `inspect()` of the four lifecycle drivers from a key of the bytes it reads; a repeat starts 0 processes against 10.
- **The pages (CP4).** [Process starts per lifecycle step](../guide/development.md#process-starts-per-lifecycle-step) states the mechanism.

## Measured process starts

Whole modules under the counter, starts as git / protocol / manager / other, and wall time under the counter, before then after:

| module | before | after |
| --- | --- | --- |
| `test_protocol_lifecycle` | 9223 / 1287 / 408 / 186, 243 s | 7524 / 1028 / 118 / 186, 239 s |
| `test_protocol_job` | 10567 / 1360 / 468 / 195, 243 s | 8663 / 1069 / 152 / 195, 199 s |
| `test_protocol_outcome` | 4077 / 464 / 148 / 92, 68 s | 3944 / 445 / 110 / 92, 63 s |
| `test_review_round_lifecycle` | 1940 / 270 / 72 / 51, 42 s | 1730 / 240 / 12 / 51, 38 s |
| `test_functional_lifecycle` | 9477 / 1318 / 314 / 162, 231 s | 7513 / 1015 / 22 / 162, 181 s |
| `test_functional_codex_route` | 1832 / 238 / 60 / 30, 46 s | 1456 / 180 / 4 / 30, 37 s |
| total | 37116 / 4937 / 1466 / 716, 873 s | 30830 / 3977 / 418 / 716, 757 s |

That is 116 s (13 percent) of local wall time across the six modules, each run once in one process by an ad hoc script that wraps `unittest` in `count_process_starts()`. It is a local measurement on one machine, not a runner measurement: the CI saving depends on runner contention (see the CI2 page) and is not claimed here. The two runs were not repeated, so the wall-time difference carries ordinary run-to-run noise.

## Bytecode (scope item 8, decision D4)

Measured at planning and not done. Compiling all of the 2.7.0 scripts (25,762 lines) takes 65 ms in one process. A query with `-X pycache_prefix` warm against the same query with `-B -E -s`, best of seven on a seeded 2.12.0 repository, took 114 against 122 ms (`describe`), 133 against 139 ms (`next-action`) and 120 against 127 ms (`verify`): about 5 percent of a query and about 50 ms of a step, below the risk of a cache directory the isolation path (`controller/workflow_contract.py`, `-B`) would have to trust. Commands: `python3 -c 'import compileall,sys; compileall.compile_dir(sys.argv[1], force=True, quiet=1)' <scripts dir>` timed once, and each query as `python -B -E -s <script> <operation>` against `python -X pycache_prefix=<dir> -E -s <script> <operation>` (after a warm-up run), best of seven.

## Open decision

O1: the owner's rule says one `-X importtime` measurement on a runner decides item 8; D4 closed it on a local measurement. If the owner wants the runner measurement, a follow-up item does it; otherwise item 8 stays closed. [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) stays open.
