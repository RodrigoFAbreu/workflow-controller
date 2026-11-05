# C12b: Self-recovery, part 1

Item id: `workflow-controller-c12b`.

## Roadmap row (moved verbatim)

Complexity: medium. Self-recovery, part 1: the Controller clears leftover test processes that hold a job draining (follow-up 8); C12 is split into three items (owner decision, 2026-10-23)

Needs: C10

Section: [11.10](../roadmap-archive.md#1110-self-recovery), follow-ups 8, 9 and 14

## Planning notes

Added at A2b from follow-up 8 of the archive: the test side was fixed by C12a (a fake worker ends itself and its group 300 s after its parent dies; every other leftover a killed test run can leave is bounded to 600 s or ends with its test). What this item covers is the Controller recognising leftovers it cannot tell apart from the worker's own work, and naming known test leftovers sooner than the three-hour drain bound.

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
