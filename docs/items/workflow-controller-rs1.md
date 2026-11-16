# RS1: Measure the CI and test speed, then decide on the Rust migration (research and pause point)

Item id: `workflow-controller-rs1`.

Not yet planned. Owner decision 2026-11-16. This is a research and decision item: no code. Complexity: small. Owner-gated: after it the owner decides go or no-go.

## Purpose

After the CI and test improvements ([CI3](workflow-controller-ci3.md), [CI2](workflow-controller-ci2.md)) and one or two more items ([BL1](workflow-controller-bl1.md) and [RF1](workflow-controller-rf1.md)), measure whether the improvement is good enough and scalable enough for continued development without 30-minute CI waits. If it is not to the owner's liking, pause both lanes (the Controller and the Workflow) and plan the migration of the Workflow, the Workflow Manager and the Workflow Controller to Rust.

The owner's order of work: CI3 (done), then CI2 (split in planning into about four parts), then BL1 and RF1, then a pause to measure with this item.

## Pause criteria

Measured from CI records, over about 10 pull requests:

- PR CI wall time at or under about 25 minutes at three shards, with no rising trend over the 10 pull requests.
- The longest shard over the average shard at or under 1.15.
- Release wait after a merge at or under about 10 minutes.
- A documentation-only change finishing in a couple of minutes.

Also measure:

- The Python interpreter launches per Controller lifecycle step and per full run, in all three repositories, counted with an `execve` wrapper such as `strace -f -e execve` or a counting wrapper.
- One `python -X importtime` profile of the vendored Workflow interpreter on a runner.

## Before any freeze

- Capture a baseline: the before and after CI metrics.
- Capture a behaviour corpus: recorded inputs and outputs from real Controller runs (event logs, state files, trailers, exit codes). It is the oracle for shadow mode.

## If the decision is go

- The migration runs in a different session from the orchestrators. No development in the Controller, the Workflow or the Manager until it is done.
- Port order, as the Manager lane set it: leaf pure byte-exact pieces first (fingerprints and digests, canonical `WORKFLOW_STATE.json` serialization, state transitions behind a golden corpus), then the Manager CLI, last the scripts adopters call, behind a shim that keeps the `python3 scripts/x.py` entry points. Published Workflow releases stay Python (dual stack).
- Then resume the lanes with the Rust version in shadow mode beside Python and compare. Decisions and outputs run on a scratch copy with recorded forge and model replies, never touching the real repository or GitHub. Compare the exact JSON, the `WORKFLOW_STATE.json` bytes, digests, trailers, exit codes and error names, and event order. Timestamps, random ids, pids and temporary paths are normalized by agreement.
- Triage every difference:
  - A Rust deviation is fixed in Rust.
  - A Python defect is only written down as a GitHub issue and kept bug-for-bug in Rust until the migration is complete, then fixed in Rust (nothing is ported twice).
  - A severe Python defect (data loss, security, a wrong merge or release) gets a Python hotfix even during the freeze, and the same fix is ported to Rust so that parity holds.
- Exit criteria for shadow mode: a long clean stretch (for example 100 runs with no unexplained difference) and every rare path observed (usage-limit holds, crashes, resume after a kill, merge, release).

## Estimates recorded

- Full Rust stack: PR CI about 10 to 20 minutes, against 35 to 60 minutes today.
- Migration: about 6 to 9 months with the gated Workflow lanes (about 3 to 5 without), plus 4 to 8 weeks of shadow mode.
- The cheap Python path (the CI2 items, in-process Workflow queries, bytecode reuse) should reach about 30 to 39 minutes, and about 20 minutes with in-process queries.
