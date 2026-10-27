# H10: name the roles and families when a mixed lane stops the external review (Revision 3)

Work item: `workflow-controller-reviewer-independence`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `d6f17eb18ba666b5a33a189b49931c1220fd1e47` ("docs: link the H10 issue from the roadmap row and the next action (#195)"), the tip of `main`, passed explicitly as `/milestone-plan d6f17eb…`.
Lifecycle authority: this repository's installed Workflow **2.22.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H10, next after H3 (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#194](https://github.com/RodrigoFAbreu/workflow-controller/issues/194).
Released baseline: `workflow-controller 1.22.2`. No registry shipped today can produce a mixed lane (D2), so no user can reach the changed text, and the squash commit is a `chore:` change that releases nothing.
Pull request title: `chore: name the roles and families when a mixed lane stops the external review (H10, #194)`

## Goal

When the gate policy asks for distinct reviewer models, the two review stages of a work item must be judged by different model families. Once a second harness can take a worker role (H4a to H4g), the routing can send a lifecycle review role to the family that `reviewer.harness` also names.

**What the code does today, read from it (revision 2 corrects revision 1's premise).** `review_mode.choose` already stops that lane before any external reviewer is launched: when the worker roles span more than one family it returns a `refused` choice in `auto`, `cross-model` and `single-provider` (and the ordinary gate in `manual`), and `_execute_step_locked` records the refusal as a `review_mode_unavailable` gate stop with a no-launch record (`controller/review_mode.py` `choose`; `controller/job.py` `_execute_step_locked`). So nothing is paid for twice, and no new refusal can stop a run that is not already stopped. What the stop lacks is **content**: its text says only that the workers are on "more than one family" and never says which role collides with the external reviewer, which is the question an operator of a mixed lane has to answer.

This milestone therefore adds no new refusal. It makes the existing mixed-lane stop **say which pair collides**: for the stage at the gate, the local review role (`review-plan` or `review-implementation`) with its harness and family, `reviewer.harness` with its family, and whether the two are of one family. The stop, its gate code, its record, its exit path, `manual`'s ordinary gate, and every other outcome of `choose` are unchanged. No setting, gate code, review mode, record field or exit code is added.

## What the code compares (the first job of this plan)

Read from the code, not assumed:

- `distinct_reviewer_models` is a Workflow gate-policy requirement that is a rule **between the two review stages** of one work item: the local stage's `Reviewer model:` header and the external stage's must differ in vendor. The Controller never evaluates it (D3).
- The local stage is a routed worker role: `review-plan` for the plan stage and `review-implementation` for the implementation stage (`controller/routing.py`, adapter role `worker`). Its family is the family of the adapter its route names (`routing.RoutingOptions.resolve(role).harness`, then `harness.get(name).family`, H1/H2).
- The external stage is `reviewer.harness`; its family is `review_mode.harness_family(name)` (the registry's `REVIEWER_FAMILY`).
- `review_mode.choose` compares the external reviewer with the **set of all worker families** (H2): a reviewer of the workers' single family reads as unset, and workers of more than one family stop every mode but `manual` (a `refused` choice, `review_mode_unavailable`).

## Decisions

Each decision states one principle.

- **D1. The pair is the stage's local review role and the external reviewer, not a worker and the role that reviews it.** That is the pair `distinct_reviewer_models` names. The literal reading of the roadmap row, a reviewer against "the worker it reviews", would compare `review-plan` with `milestone-plan` or `milestone-implement`, which are Claude Code in the built-in routing and in every shared settings file in use, so it would flag the default; the Workflow does not ask for it either. The row is recorded as read this way in CP2.
- **D2. Only the existing mixed-lane stop changes, and only its text.** The new text is produced exactly where `choose` already returns the mixed-family `refused` choice (more than one worker family, and a mode other than `manual`). It is built in `job._choose_review`, which replaces `choice.reason` when `choice.kind == "refused"` and `len(workers) > 1`, keeping `kind`, `harness`, `lowered` and `signal` as `choose` returned them. Because `manual` returns the ordinary gate before the mixed-family check, `--review-mode manual` is untouched by construction: it reaches the ordinary gate, as today, and is never refused (review of revision 1, B1). With one worker family nothing changes: the reviewer of that family reads as unset, a documented outcome of today.
- **D3. The text reports the pair and does not decide anything.** For the stage at the gate (D1) it names the local role, its harness and family, and `reviewer.harness` and its family when set; it says the two are of one family when they are equal (the `distinct_reviewer_models` rule would refuse the approval for that stage) and that the pair is distinct when they differ (the stop is then caused by the other roles). Families come from the adapters and the routed harness, never from a model-name prefix. It does not read the gate policy: `verify` is not asked, so the lowered-policy signal is read by `choose`'s own path only (`needs_signal` returns `False` for a mixed lane) and by nothing new, so `verify` is never asked twice (revision-1 Optional finding).
- **D4. The remedies stay true in every mode where the text appears.** The text appears only in `auto`, `cross-model` and `single-provider`, so naming `--review-mode manual` (as `choose`'s text already does) is correct there, and it is not named in `manual`, where the text never appears. The remedies are: route every worker role to harnesses of one family (and, for a collision, route the local review role or `reviewer.harness` to another family), or choose `--review-mode manual`.
- **D5. The pure function takes the routing options, the reviewer and the stage, and does no I/O.** `review_mode.mixed_lane_reason(options, reviewer, stage)` returns the text. `explain` shows it through `review_mode_report`'s existing `reason` field, with no new field and no new catch.
- **D6. Everything that loads today keeps loading, and a test shows it.** The built-in routing, the lane's committed routing fixture (`tests/fixtures/lane_routing.json`) and the shared-settings shape with `reviewer.harness` unset, `claude` or `codex` yield the same `Choice` from `_choose_review` as before (one worker family, so the text is not used). The only changed outcome needs a patched registry in which a harness other than Claude Code takes a worker role, the way `tests.test_review_mode.codex_workers` builds it, and there only `reason` differs.
- **D7. Nothing a user can reach changes, so nothing is released.** The squash commit is `chore:`: the repository's release policy maps `chore:` to no release. The existing tests stay unchanged; the only new test file is `tests/test_review_independence.py`.

## Out of scope

A Codex worker (H4a to H4g); fallback chains (H9); changing the Workflow's own distinct-model rule or the review modes; loosening `choose`'s mixed-family stop, which will block a deliberately mixed lane (workers on two families) until the follow-up below; a new refusal, setting, routing key, gate code, record field or exit code; the functional tester's routing.

**Follow-up for the owner (not done here).** Because `choose` compares the external reviewer with all worker families, a lane that runs the implementation worker on Codex and the reviewers on Claude stops at every external gate even though the per-stage pair is distinct. Replacing that set comparison with a per-stage pair (the pair this milestone only reports) is the change that makes a mixed lane usable and that would give an independence rule real work to do; it alters a review-mode outcome, so it is left to H4 or H9 and to an owner decision, and recorded as an issue at acceptance.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The mixed-lane text, its use at the review gate and its tests | - | 2 | 1 |
| CP2 | The guide, the troubleshooting page and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The mixed-lane text, its use at the review gate and its tests

**Files:** `controller/review_mode.py`, `controller/job.py`, `tests/test_review_independence.py`

D1 to D6. In `controller/review_mode.py`: a constant `LOCAL_REVIEW_ROLE` (`{"plan": routing.REVIEW_PLAN, "implementation": routing.REVIEW_IMPLEMENTATION}`) and `mixed_lane_reason(options, reviewer, stage)`, a pure function returning the text of D3 and D4 (it begins with `choose`'s existing sentence, so the stop stays recognisable). In `controller/job.py`: `_choose_review`, after `review_mode.choose`, returns a new `Choice` with the same fields and the new `reason` when `choice.kind == "refused"` and `len(workers) > 1` (built with a `Choice(...)` call, because `dataclasses.replace` is flagged by the write-containment scan), and changes no other line. `review_mode_report` and `_execute_step_locked` are not edited: they already pass `choice.reason` to the gate stop and to `explain`.

Tests, all in the new `tests/test_review_independence.py`, none needing a sandbox or a real `claude` (a patched registry as `tests.test_review_mode.codex_workers` builds one):
- nothing that loads today changes: with the built-in routing, `routing.load_routing_config` of `tests/fixtures/lane_routing.json` and `reviewer.harness` unset, `claude` and `codex`, both stages and every mode, `job._choose_review` (with `job._external_review_stage` patched) returns the `Choice` `review_mode.choose` returns, and `mixed_lane_reason` is never called;
- the relation to `choose` is pinned: with the patched registry (`review-plan` routed to `codex`, another worker role on Claude Code, so two families) and `reviewer.harness=codex`, for each of `auto`, `cross-model` and `single-provider` `job._choose_review` returns a choice of kind `refused` with the `harness`, `lowered` and `signal` that `choose` returned, and only `reason` differs, that reason naming `review-plan`, `codex`, `openai`, `reviewer.harness` and the remedies; for `manual` it returns the ordinary gate (`GATE`), is never refused and does not call `mixed_lane_reason` (B1);
- per stage: the same lane at stage `implementation` with `review-implementation` still on Claude Code says the pair is distinct, and at stage `plan` says the two are of one family; an unset `reviewer.harness` names the local role and its family only;
- `verify` is not asked by the new text: the signal reader is not called for the mixed lane (D3);
- through the job: `job.review_mode_report` returns `choice: refused` with the new text as `reason` for the collision case and `choice: gate` for `manual`, and an `execute_step` run on the collision case ends as today, a `gate_blocked` no-launch record whose gate is `review_mode_unavailable` and whose text is the new one (a `tests.test_review_mode.JobChoiceTest`-style fixture, in the new file), while the same run with `--review-mode manual` reaches the ordinary gate.

The run list: `tests.test_review_independence`, `tests.test_review_mode` (unedited, including `WorkerFamiliesTest`), `tests.test_routing` (unedited), `tests.test_harness`, and the review job modules that drive `_choose_review` (`tests.test_review_job`), all green. The full suite is the operator's rerun; two Git 2.56 trailer tests fail locally and are known.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guide, the troubleshooting page and the roadmap

**Files:** `docs/guide/runtime.md`, `docs/common-problems.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D1, D2, D4, D7. `docs/guide/runtime.md`: in the paragraph on `reviewer.harness` and in the `cross-model` row of the review-modes table, which already say that a mixed lane stops `auto`, `cross-model` and `single-provider` with `review_mode_unavailable`, add one sentence: the stop's text names the stage's local review role, `reviewer.harness` and their families and says whether they are of one family; `manual` is unaffected. `docs/common-problems.md`, section "A run stopped at review_mode_unavailable": add a third cause, worker roles on more than one family (the mixed-lane stop `choose` has refused since H2), and say that the message now names the stage's local review role and `reviewer.harness` with their families; the page then agrees with `docs/guide/runtime.md` (review of revision 2, Optional finding). The documented outcome (the gate stop, no exit `20`) is stated once and stays consistent in each mode. `docs/ROADMAP.md`: row H10 marked complete and reworded to what it did (names the colliding pair in the mixed-lane stop; the per-stage replacement of the set comparison is the follow-up), the epic's prose summary (the sentence that calls H10 "the independence rule in routing") reworded in the same edit so the roadmap describes H10 one way, and the "Next, in order" item 2 naming the item after it; `docs/ACTIVE_MILESTONE.md` points at the next item. `python3 tools/check_docs.py` passes.
<!-- /CP2 -->

## Functional review notes

Every flow is unit level and runs in the tester's sandbox with no `claude`: the mixed-lane, per-stage, `manual` and unchanged-lane tests of `tests.test_review_independence`, and the unedited `tests.test_review_mode`. The full suite is not a flow.
