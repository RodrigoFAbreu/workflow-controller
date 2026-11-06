# H4e2b: Codex worker, step 5c

Item id: `workflow-controller-h4e2b`.

Not yet planned. Route the two review roles (`review-plan`, `review-implementation`) to Codex through the routing config, using the recorder and the read-only run that H4e2 builds. This item needs H4e2. It was split from H4e2 on 2026-11-06 ([#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315)). Complexity: medium.

## Scope, one mechanism

The one mechanism is routing. This item:

- routes the two roles and wires the job (checkpoint 3 of the H4e2 redraft);
- adds the adversarial safety tests of the routed path (credentials, symlink, submodule, hook, crafted ref), a test for every refusal;
- adds the opt-in operator live proof on a scratch repository;
- adds the cost table (the usage cost of the Codex review loop against the weekly hold);
- updates the pages (checkpoint 4 of the redraft).

## Out of scope

The two `apply-*` roles (H4e3), `milestone-implement` (H4f).

## Planning notes

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
