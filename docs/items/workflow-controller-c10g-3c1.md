# C10g-3c1: The Codex tester chosen through H1's per-role routing, so there is no separate tester-harness setting

Item id: `workflow-controller-c10g-3c1`.

## Roadmap row (moved verbatim)

Complexity: low ([#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223)). The Codex tester chosen through H1's per-role routing, so there is no separate tester-harness setting (routing accepts `codex` for `functional-tester` at every entry point with the model `gpt-6.1-sol` and an effort named explicitly (no Codex defaults), `job._run_functional` dispatches on the route's harness (usage provider, credential, record fields and `launch_codex_functional`), the tester stays outside the reviewer-independence rule, and the guides say how to run it; the Claude path is unchanged; a `feat:` change, released as a minor version)

Needs: C10g-3b2b, H1, H9b

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
