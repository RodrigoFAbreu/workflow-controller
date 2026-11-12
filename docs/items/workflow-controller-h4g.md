# H4g: Codex worker, step 7

Item id: `workflow-controller-h4g`.

## Roadmap row (moved verbatim)

Complexity: low. Codex worker, step 7: the remaining roles (`milestone-plan`, `apply-functional-review`, `apply-pr-review`, `satisfy-gate`, `prepare-functional-review`, `record-manual-*`) and the documentation; every role can run on either harness that declares it

Needs: H4f

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## What it inherits from H4f

The first thing this item inherits is `milestone-implement-self-review`. H4f left it on the `worker` adapter role and on Claude Code only, because it writes the implementation bundle's author-written, ignored files and runs the generator's `implementation` stage, which needs a carry of ignored files and a guarded generator run for that stage (the apply roles' carry and [H4e3b](workflow-controller-h4e3b.md)'s generator run are the mechanisms to reuse). H4f's surface list (`codex_worker_landing.SURFACE_DIRECTORIES`) and the claim reader (`controller/checkpoint_claims.py`) are the other things it builds on. See [the checkpoint role on Codex](../guide/harnesses.md#the-checkpoint-role-on-codex).
