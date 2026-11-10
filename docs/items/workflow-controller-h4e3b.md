# H4e3b: Codex worker, step 5e

Item id: `workflow-controller-h4e3b`.

Not yet planned. Run the managed repository's generator (`scripts/prepare-ai-review.sh`) safely after the Controller lands a Codex worker's commits. This item needs H4e3. It was split from H4e3 on 2026-11-10 ([#334](https://github.com/RodrigoFAbreu/workflow-controller/issues/334)) by owner decision. Complexity: medium.

## Scope, one mechanism

What the Controller may execute after a landing, what it must compare first, and how it refuses the rest. It needs its own safety design, written before any plan.

## Inputs: the two findings

- **H4E3-R5-001 (plan round 5 of H4e3).** A worker can land a `scripts/sitecustomize.py`; the managed generator, run by the Controller right after the landing, imports it, so worker-written code runs with the Controller's authority.
- **H4E3-R6-001 (plan round 6 of H4e3).** The same class by other import-path routes: a module that shadows a standard-library module, and a forged `.pyc` that Python loads in place of its source. A name-status diff of the landing is the comparison that the design has to decide on, together with the import path and `.pyc` cases.

## Out of scope

The carry in and out and the recovery of the two apply roles (H4e3), `milestone-implement` (H4f).

## Planning notes

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
