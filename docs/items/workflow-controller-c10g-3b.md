# C10g-3b: The sandbox bridge

Item id: `workflow-controller-c10g-3b`.

## Roadmap row (moved verbatim)

Complexity: medium ([#222](https://github.com/RodrigoFAbreu/workflow-controller/issues/222)). The sandbox bridge: the Codex tester's clone and layout built from the functional review's candidate (the tested bytes pinned to the head, the stand-ins on `PATH`, the output folder in the scratch area), the binding check after a run and the tester's prompt for Codex, offline only (the first half of the old C10g-3b, split because the launch is larger than one item) (`controller/codex_bridge.py` with `build_bridge`, `check_bound` and `prompt`, sharing `clone_candidate`, `bound_differences` and `tester_prompt` with the Claude tester; nothing calls it until C10g-3b2a and C10g-3b2b; a `chore:` change with no release)

Needs: C10g-3a

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
