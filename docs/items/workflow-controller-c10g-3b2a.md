# C10g-3b2a: The Codex tester's run

Item id: `workflow-controller-c10g-3b2a`.

## Roadmap row (moved verbatim)

Complexity: medium ([#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)). The Codex tester's run: `codex_tester.run_tester` runs the launch controls and `codex exec` on the bridged sandbox under a deadline, classifies the outcome (`ran`, `timeout`, `limit`, `failed`), returns the final message, the readings and the credential copy-back in the Claude launch's result shape, and removes the credential copy on every path; one opt-in live test runs a real turn (nothing calls it until C10g-3b2b; a `chore:` change with no release)

Needs: C10g-3b

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
