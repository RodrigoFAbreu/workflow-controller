# RR1: retry before the false release-without-tag collision gate (Revision 1)

Work item: `workflow-controller-rr1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `58b9698f8388cd67d83dde1a25167255f4877b25` ("docs: add RR1, retry before the false release-without-tag gate (#388)"), the tip of `main`, passed explicitly as `/milestone-plan 58b9698f8388cd67d83dde1a25167255f4877b25`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-rr1` (`docs/backlog.yaml` order 103, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-rr1.md`. It has no dependencies.
Released baseline: the latest tag. The change edits a `controller/` module, so the squash commit is a `fix:` and publishes a patch release.
Pull request title: `fix: retry before the false release-without-tag collision gate (RR1)`

## Goal

At the Controller's release wait, `release_txn.classify` read `COLLISION_RELEASE_WITHOUT_TAG` four seconds after a release was published (job `20261119T174858Z-14e48134`): the release was visible and the tag was not yet. A minute later the tag was there and the release was fine, so the gate was false and cost a rerun. Every `feat:` or `fix:` milestone merged under the auto-merge opt-in exercises the release wait, so any of them can hit it.

The fix is one mechanism: **the release wait re-reads before it accepts the collision.** When a classification says a release exists without its tag, the Controller waits and classifies again, a bounded number of times, and raises the collision only if the tag is still missing after the last read.

## What exists

- `controller/release_txn.py` `classify` reads the remote's tags with `ls-remote`, then reads the release with the forge. The row `tag_commit is None and found is not None` returns `COLLISION_RELEASE_WITHOUT_TAG`. `gh release create` makes the tag and the release in one call, so the row is false when the tag read is older than the release read.
- The Controller's release wait (`controller/milestone_branch.py`, `_decide_release`) calls `classify(rctx, m, verify_assets=False)` three times and turns a collision into `release_failed`. `rctx` is built in the function that applies the wait, from `ReleaseContext`.
- The release workflow in CI also calls `classify`, and there a collision is real: the same run created the tag and the release. It must keep failing at once.
- `milestone_branch._sleep` is the module's one patch point for sleeping (`waiting_preflight`); tests replace it.

## Decisions

- **D1. A bounded retry, only on the Controller's release wait.** `classify` gains a keyword `tag_retry: bool = False`. When true and the first classification is `COLLISION_RELEASE_WITHOUT_TAG`, it sleeps and classifies again (the whole function, so the tags are fetched and read again and the release is viewed again), once per entry of `TAG_VISIBILITY_DELAYS`, stopping as soon as the state is another one. The state after the last read is returned unchanged, so a tag that never appears is the collision, as today. With `tag_retry` false nothing changes, which keeps CI and `release-tools` failing at once and every existing collision test as it is.
- **D2. The bound is a named constant.** `TAG_VISIBILITY_DELAYS = (2.0, 4.0, 8.0, 16.0, 30.0)` seconds in `controller/release_txn.py`, 60 seconds in all, five re-reads. It is a constant, not a setting: one mechanism, no new surface.
- **D3. The sleep is injectable.** `ReleaseContext` gains `sleep: Callable[[float], None] = time.sleep`. `milestone_branch` builds `rctx` with `sleep=_sleep` (its existing module patch point), so a test replaces one name and never waits.
- **D4. All three release-wait classifications retry.** `_decide_release`'s three `classify` calls pass `tag_retry=True`. A collision is a one-time state that either clears or does not, and the later calls cost nothing when the first settled.
- **D5. No change to the outcome table or the gate.** A tag that never appears still ends as `release_failed` with the `COLLISION_RELEASE_WITHOUT_TAG` detail, now after about a minute. No event, no setting, no new state.

## Open decisions

This repository has no `docs/TECHNICAL_DECISIONS.md`; no decision-record "Open decision" row is touched, and no owner decision is open.

## Checkpoints

<!-- CP1 -->
### CP1: re-read before the collision

Implement D1-D4: `TAG_VISIBILITY_DELAYS`, the `sleep` field, the `tag_retry` keyword on `classify` (the existing body becomes a private single read), and the three calls in `_decide_release`.

Tests, with a fake forge whose tag appears after a delay (the injected sleep creates the tag): the tag shows on the second read, so the state is `RELEASE_DUE` or `ALREADY_RELEASED` and the sleeps are the first delay only; it shows on the last read; it never shows, so the collision is returned after exactly `len(TAG_VISIBILITY_DELAYS)` sleeps with those delays; a classification that is not a collision never sleeps; `tag_retry` false never sleeps (the existing collision tests stay). In the release-wait tests: a release wait whose tag shows late settles instead of `release_failed`, and one whose tag never shows ends `release_failed` with the collision detail.

**Files:** `controller/release_txn.py`, `controller/milestone_branch.py`, `tests/test_release_txn.py`, `tests/test_pull_request_lifecycle.py`

**Tests:** `tests.test_release_txn`, `tests.test_release_tools`, `tests.test_pull_request_lifecycle`, `tests.test_package_structure`, `tests.test_write_containment`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: the pages and the item record

Document the retry on the three pages the item names: the release wait in `docs/guide/milestone-branches.md` (the `COLLISION_RELEASE_WITHOUT_TAG` case of "any other classification"), `docs/common-problems.md` (a short entry: a collision that persists after the wait is real) and the outcome table of `docs/guide/ci-and-releases.md` (CI fails at once, the Controller's wait re-reads first). Record the status, the plan link and the bound on `docs/items/workflow-controller-rr1.md`. `docs/backlog.yaml` and `docs/ROADMAP.md` are unchanged: the item stays `next` until acceptance.

**Files:** `docs/guide/milestone-branches.md`, `docs/common-problems.md`, `docs/guide/ci-and-releases.md`, `docs/items/workflow-controller-rr1.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-rr1`.
<!-- /CP2 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | A tag that becomes visible within the bound after the release is never reported as a collision by the release wait, and one that never does still is | CP1 |
| R2 | CI and `release-tools` still fail a collision at once | CP1 |
| R3 | The pages and the item record state the retry and its bound | CP2 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Re-read before the collision | - | 2 | 1 |
| CP2 | The pages and the item record | CP1 | 1 | 1 |
