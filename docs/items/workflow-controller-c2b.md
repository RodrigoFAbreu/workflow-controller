# C2b: CI time and flakiness

Item id: `workflow-controller-c2b`.

## Roadmap row (moved verbatim)

CI time and flakiness: bring a pull request's CI back to 15-20 minutes (about 85 since C10c; 46 after the first timings refresh of 2026-10-22): check catalogue determinism on `main` only, share one built sandbox across the functional test cases, make other slow tests cheaper, and fix the flaky `FollowCommandTest` ([#119](https://github.com/RodrigoFAbreu/workflow-controller/issues/119), [#121](https://github.com/RodrigoFAbreu/workflow-controller/issues/121)). Owner rule, 2026-10-22: when a pull request's CI goes over 30 minutes, improving it is the next item (accepted 2026-10-22 by gate policy, PR #123, no release: #121 fixed; the whole-catalogue checks run on `main` and gate the release, six shards, shared lifecycle snapshots, refreshed timings; a pull request's complete check took 19.3 minutes at the acceptance head, within the target, so #119 is closed)

Section: [11.2](../roadmap-archive.md#112-ci-reliability)
