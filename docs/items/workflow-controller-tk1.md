# TK1: The user-only command scan catches a name followed by punctuation or behind =

Item id: `workflow-controller-tk1`.

## Roadmap row (moved verbatim)

(a `fix:` change, a patch release). Complexity: low ([#247](https://github.com/RodrigoFAbreu/workflow-controller/issues/247)). The scan that keeps a model from running a user-only command named inside a task or argument string missed a name followed by `!`, `?`, a bracket or a quote, or behind `=` (found by the H4a review; the shipped Claude launch had the same gap); `worker._task_tokens` now splits on every character a name or path cannot contain, with a test per spelling in the Claude launch and the Codex renderer; a name inside a longer word or path is still not refused

Needs: H4a

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
