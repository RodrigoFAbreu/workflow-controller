# H4e2b: Codex worker, step 5c

Item id: `workflow-controller-h4e2b`.

Planned and implemented (plan `docs/ai-workflow/CONTROLLER_H4E2B_PLAN.md`, four checkpoints). Route the two review roles (`review-plan`, `review-implementation`) to Codex through the routing config, using the recorder and the read-only run that H4e2 builds. This item needs H4e2. It was split from H4e2 on 2026-11-06 ([#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315)). Complexity: medium.

## Scope, one mechanism

The one mechanism is routing. This item:

- routes the two roles and wires the job (checkpoint 3 of the H4e2 redraft);
- adds the adversarial safety tests of the routed path (credentials, symlink, submodule, hook, crafted ref), a test for every refusal;
- adds the opt-in operator live proof on a scratch repository;
- adds the cost table (the usage cost of the Codex review loop against the weekly hold);
- updates the pages (checkpoint 4 of the redraft).

## Out of scope

The two `apply-*` roles (H4e3), `milestone-implement` (H4f).

## Decisions and status

- D1/D2: the two roles run as the adapter role `local-reviewer`; `(codex, local-reviewer)` is a launcher that never spawns; routing is opt-in and the default is unchanged.
- D3/D4: a routed review runs `run_review` inside the job's record, one status per ending, `recover` at every recovery site.
- D5: the routed path is tested adversarially through `execute_step` (`tests/test_codex_review_safety.py`).
- D6: `tests/test_codex_review_live.py` is the opt-in live proof, one Codex turn per stage.
- D7/D8: the status table, the cost table and the routing are on [harnesses](../guide/harnesses.md#routing-the-two-local-review-roles-to-codex).

**Live proof status: not run.** It spends the operator's Codex usage and the squash does not wait for it, so
`tests/harness_contract/codex/review_role.txt` does not exist and every measured cell of the cost table reads `not measured`.
Run `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_review_live -v`, copy its output into that file and fill the table from it.

## Planning notes

Planned in `docs/ai-workflow/CONTROLLER_H4E2B_PLAN.md`. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
