# W2: move this repository to Workflow 2.29.6

Item id: `workflow-controller-w2`.

Status: next (not yet planned). Move this repository from Workflow 2.29.3 to 2.29.6. Owner decision (2026-11-15): 2.29.4, 2.29.5 and 2.29.6 were released since the previous move ([W1](workflow-controller-w1.md)). Priority: medium. Complexity: low (a chore).

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## What each release brought

From the Workflow Manager's notes:

- 2.29.4 (fix): the retention engine's unreadable-path cases end in named refusals with exit 2 instead of tracebacks (the retention engine script changed).
- 2.29.5 (fix): the plain acceptance report returns a named blocked report, `stored_pr_fact_unresolvable`, when the stored pull-request fact's head cannot be resolved; this is one new blocker code in that report, plus message wording fixes.
- 2.29.6 (fix): the implementation bundle contract now has `TEST_RESULTS.md` carry one section per implementation revision and `IMPLEMENTATION_SUMMARY.md` one entry per review round. This was the Controller lane's own finding (LOCAL-IMPL-R2-001 and R3-001, workflow#142). It is wording and contract only: the Controller still reads only the two file names.
- The protocol stays 1.5 in all three.

## Scope

One mechanism: the Workflow move, following [Compatibility](../compatibility.md) and [Move a repository to a newer Workflow](../update.md#move-a-repository-to-a-newer-workflow), with the precedent of W1.

- `workflow-manager update .`, run by the operator (the Workflow Manager lane, with the owner's OK); a worker must not run it.
- Then the Controller's own checks: `workflow-manager verify .`, `controller inspect` (with `PYTHONPATH` set), and the Workflow's own protocol tests.
- Docs: [Compatibility](../compatibility.md) lists the Workflow releases that keep protocol 1.5, and [Update](../update.md) describes the move; extend them with 2.29.4, 2.29.5 and 2.29.6.

## Lessons from W1

- The move added `scripts/workflow_gate_policy_test.py`, which reads `**/*`, and that needed a `tools/ci_changes.py` `MENTIONS` entry; the repository's CI selection tests may need updating again.
- The tester sandbox had no `workflow-manager` on `PATH` (since handled by TS1).
- Do the move when the lane is idle between items, not while a run is active.

## Out of scope

Adopting new behaviour in the Controller's own steps and any protocol change.
