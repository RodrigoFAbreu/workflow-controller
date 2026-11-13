# W1: move this repository to Workflow 2.29.3

Item id: `workflow-controller-w1`.

Not yet planned. Move this repository from Workflow 2.27.0 to 2.29.3. Owner request (2026-11-13): "upgrade the workflow when possible". Complexity: low (a chore).

## Scope

One mechanism: the Workflow move, following [Compatibility](../compatibility.md) and [Move a repository to a newer Workflow](../update.md#move-a-repository-to-a-newer-workflow), with the precedent of the 2.27.0 move (PR #311, "chore: move this repository to Workflow 2.27.0").

- Workflow 2.28.0 to 2.29.3 added the plain commands `--write-findings-record`, `--commit-gate-policy-floor` and `--finding-dispositions`, and hardened `--acceptance-gate` and `--satisfy-acceptance`.
- The protocol stays 1.5 and, per the Workflow Manager's notes, no Controller change is needed; the compatibility page's "Workflow moves" entries are extended with the new releases.
- Do it when the lane is idle between items, not while a run is active.

## Out of scope

Adopting the new commands in the Controller's own steps (a separate item if wanted) and any protocol change.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
