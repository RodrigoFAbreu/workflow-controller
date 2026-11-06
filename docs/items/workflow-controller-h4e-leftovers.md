# H4e leftovers: the review findings left out of the Codex worker clone

Item id: `workflow-controller-h4e-leftovers`.

A `chore:` change, with no release. The five items of [#306](https://github.com/RodrigoFAbreu/workflow-controller/issues/306) are fixed before H4e2 routes any real role to the Codex worker. They are one mechanism: the clone's evidence, durability and tests. The planning rules apply: at most one new mechanism, releasable alone, and a split at the first repeated finding class.

1. **A kept clone's path is not always in the evidence the guides send the operator to.** `docs/guide/harnesses.md` and `docs/guide/workers.md` say `rm -rf` of the exact path in `reconciliation_evidence.clone`; a `LAUNCHED` record that ends `FAILED` (a Workflow error reason) or `FINISHED` in `_reconcile_codex_worker` (`controller/job.py`), and some other endings, keep a clone without naming it there (it is still in `codex_worker.clone`). Name it for every ending that keeps one.
2. **Window (b) removes the tree before anything durable says so**: `_apply_codex_landing` persists nothing, so a second kill after the removal can report a kept clone that is gone. Persist the fact before removing, or make the report check the path.
3. **The landing tests share the job id `job1`**, and that id is the ownership tag `resume` scans for, so overlapping tests can fail (the shared-tag flake class of #259); `KilledLandingFixture`/`LandingFixture` should use per-test job ids as the plan's Risks section says.
4. **The drifted `resume` of a `LAUNCHED` Codex record has no test of its own** (the `elif` branch added in round 3, `controller/job.py` around line 3114); the behaviour was measured correct.
5. **Untraced full-suite failures in the implementer's sandbox**: two runs at `02269f8` failed in `FakeLaunchTest` (`the composing command did not run`, `features list did not run`) in `tests.test_review` and `tests.test_review_claude`. CI is green at the accepted head (all six shards), so the cause is probably the sandbox; confirm it.

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Complexity

Complexity: low to medium.
