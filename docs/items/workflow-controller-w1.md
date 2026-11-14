# W1: move this repository to Workflow 2.29.3

Item id: `workflow-controller-w1`.

Status: implementing (2026-11-14); CP1 is complete and CP2 records the move. Plan: [CONTROLLER_W1_PLAN.md](../ai-workflow/CONTROLLER_W1_PLAN.md). Move this repository from Workflow 2.27.0 to 2.29.3. Owner request (2026-11-13): "upgrade the workflow when possible". Complexity: low (a chore).

The move ran through the Workflow Manager (`update`, `verify`, `inspect`) at `IMPLEMENTING`, between checkpoints, with the lane stopped by `workflow-controller step`, and CP1's checkpoint commit landed it. Verification at CP1: `workflow-manager verify .` reports the installation matches 2.29.3; the installed `workflow-controller inspect .` and `python3 -m controller inspect .` print the item with no refusal; `python3 tools/check_docs.py` passes; `python3 tools/run_tests.py` ran 6605 tests and failed four, all known environmental or cross-shard causes (the three `tests.test_fake_codex_contract` cases, and one codex-apply test that collided on a shared fake `codex exec` tag and passes alone).

## Scope

One mechanism: the Workflow move, following [Compatibility](../compatibility.md) and [Move a repository to a newer Workflow](../update.md#move-a-repository-to-a-newer-workflow), with the precedent of the 2.27.0 move (PR #311, "chore: move this repository to Workflow 2.27.0").

- Workflow 2.28.0 to 2.29.3 added the plain commands `--write-findings-record`, `--commit-gate-policy-floor` and `--finding-dispositions`, and hardened `--acceptance-gate` and `--satisfy-acceptance`.
- The protocol stays 1.5 and, per the Workflow Manager's notes, no Controller change is needed; the compatibility page's "Workflow moves" entries are extended with the new releases.
- Do it when the lane is idle between items, not while a run is active.

## Out of scope

Adopting the new commands in the Controller's own steps (a separate item if wanted) and any protocol change.
