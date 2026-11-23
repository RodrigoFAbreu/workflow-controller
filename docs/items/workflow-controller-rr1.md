# RR1: retry before the false release-without-tag collision gate

Item id: `workflow-controller-rr1`.

Status: implemented on the milestone branch (two checkpoints); plan: [CONTROLLER_RR1_PLAN.md](../ai-workflow/CONTROLLER_RR1_PLAN.md) (revision 1). The release wait re-reads a `COLLISION_RELEASE_WITHOUT_TAG` classification up to five times, after 2, 4, 8, 16 and 30 seconds (60 seconds, `TAG_VISIBILITY_DELAYS`), before accepting it; CI and `release-tools` still fail at once. The item stays `next` until acceptance. Owner request 2026-11-19. Priority: high. Complexity: low.

## Why

Source: the Manager lane report of 2026-11-19, job `20261119T174858Z-14e48134`, run1107. At the release wait the Controller gated `release_failed / COLLISION_RELEASE_WITHOUT_TAG: a release named v1.23.0 exists without the tag` at 17:48:58, four seconds after the release was published (17:48:54). `gh release create` creates the tag and the release in one call, and a minute later tag `v1.23.0` pointed at main `c1fb8d0` and the release (3 assets) was fine. Re-running the Controller classified it again and closed out cleanly.

So it is a read race between the release becoming visible and the tag becoming visible, and the gate is false. It costs a rerun, not a failure.

Every `feat:` or `fix:` milestone merged under the auto-merge opt-in exercises the release wait, so the false gate can hit any of them. [UF1](workflow-controller-uf1.md) (`feat:`) is the first one in this repository.

## Scope

One mechanism: a short, bounded retry with backoff (for example a few reads over about a minute, the bound set by a named constant) before the release-wait classification raises `COLLISION_RELEASE_WITHOUT_TAG`.

- The check lives in `controller/release_txn.py` (the `COLLISION_RELEASE_WITHOUT_TAG` result in the classification, where `tag_commit is None and found is not None`). The tests are in `tests/test_release_txn.py` and `tests/test_release_tools.py`.
- A test with a fake forge whose tag appears after a delay: the tag shows up on the second or third read, so there is no collision; the tag never shows up within the budget, so the collision is raised as today.
- Documentation: [Auto-merge and the release wait](../guide/milestone-branches.md#auto-merge-and-the-release-wait), [Common problems](../common-problems.md) and the outcome table in [CI and releases](../guide/ci-and-releases.md).

The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
