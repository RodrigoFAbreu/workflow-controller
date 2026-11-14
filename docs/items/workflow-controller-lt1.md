# LT1: workers run the selected tests per checkpoint and leave the full suite to CI

Item id: `workflow-controller-lt1`.

Not yet planned. Owner decision 2026-11-14. Complexity: low to medium. Priority: high (owner).

## Why

Controller workers run the full suite (`python3 tools/run_tests.py`, 8 shards plus scenario children) at every checkpoint, and sometimes two lanes do so at once. That loads the owner's machine (load about 12 on 16 cores) while CI already runs the full suite on every pull request (about 19 minutes).

## Scope

One mechanism: at each checkpoint the worker runs only the tests selected for what changed, not the full suite. The selection is the one `tools/ci_changes.py` computes, or the test modules the plan names. The full suite is left to the runners, with at most one local full run before the pull request. Two full suites never run at once: a lock or a documented rule between lanes.

Changes: the worker instructions and the plan's verification wording, the Controller's checkpoint verification, and the documentation that describes them.

## Trade-off

A regression outside the changed area surfaces later, in CI on the pull request, instead of at the checkpoint. `tests.test_ci_changes` guards the selection.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
