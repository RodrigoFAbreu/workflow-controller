# H4f: Codex worker, step 6

Item id: `workflow-controller-h4f`.

## Roadmap row (moved verbatim)

Complexity: medium. Codex worker, step 6: `milestone-implement` can be routed to Codex (the longest role: checkpoints, commits, self-review); a safety review and a live proof (operator-run)

Needs: H4e (reuses the tree-and-landing decision)

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Status

**Implemented on its milestone branch, awaiting review.** Plan: `docs/ai-workflow/CONTROLLER_H4F_PLAN.md` (revision 5), five checkpoints, issue [#312](https://github.com/RodrigoFAbreu/workflow-controller/issues/312).

`milestone-implement` can be routed to Codex through the adapter role `implementer`: the worker implements one checkpoint in a private clone and commits it with the Workflow's trailers, and the Controller lands the commits only after refusing `surface_touched` (a path under `scripts/`, `.workflow-manager/`, `.claude/` or `.codex/`, a link, a mode change) and `state_touched` (the state file changed outside this item's checkpoint fields). Before the clone is made, a claim, an open amendment or a dirty managed tree is refused (`managed_*`). A clone that holds unfinished work is kept and named. `milestone-implement-self-review` stays on Claude Code. See [the checkpoint role on Codex](../guide/harnesses.md#the-checkpoint-role-on-codex) and [common problems](../common-problems.md#a-codex-checkpoint-was-refused-surface_touched-state_touched-or-managed_).

**Live proof: not run.** `tests/test_codex_implement_live.py` is the opt-in proof (`CONTROLLER_LIVE_CODEX_TESTS=1`, skipped on `CI`, one real Codex turn). It was collected and skipped offline. `tests/harness_contract/codex/implement_role.txt` does not exist until the operator runs it, and whether a worker can run a checkpoint's narrow test inside the sandbox profile is unanswered; a failure there is a follow-up issue, not a change of this item's scope.
