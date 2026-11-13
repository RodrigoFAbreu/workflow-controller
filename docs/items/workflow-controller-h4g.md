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

## What H4g did

H4g routes **`milestone-implement-self-review`**, the wrap-up role, to Codex. It runs as the checkpoint role's adapter role
(`implementer`); the landing takes a named state profile whose fields are derived from the Workflow's two writers
(`enter_self_reviewing_implementation`, `record_bundle_generation`); the role runs on a clean managed tree through the carry of the
implementation stage's five out members, never carrying a derived file; and the Controller regenerates the implementation
bundle with the guarded generator run only when the landing holds the item's generation record, so a partial self-review stops
without a generator run. The default is unchanged. See [the wrap-up role on Codex](../guide/harnesses.md#the-wrap-up-role-on-codex)
and [what was refused](../common-problems.md#a-codex-wrap-up-was-refused-or-stopped-early). The tests are
`tests/test_codex_selfreview_job.py` (clean runs, the recovered role, the partial case), `tests/test_codex_selfreview_safety.py`
(every plant, on each side of the landing) and the opt-in `tests/test_codex_selfreview_live.py`, which **has not been run live**
(`tests/harness_contract/codex/selfreview_role.txt` does not exist).

## What stays open

The other roles of the row above (`milestone-plan`, `apply-functional-review`, `apply-pr-review`, `satisfy-gate`,
`satisfy-gate-acceptance`, `prepare-functional-review` and `record-manual-plan-review` / `record-manual-implementation-review`)
are **not** routable to Codex after this item, and they **remain open under H4g**: the plan's proposal to split them into
follow-up backlog items (state-only commit roles first, then `milestone-plan`, then the functional and pull-request roles) is the
owner's decision and no split change has landed, so this page links no follow-up item and the H4g backlog row is not marked
done by this item's code.
