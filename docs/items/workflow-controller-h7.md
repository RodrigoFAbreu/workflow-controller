# H7: Build verification declared by the adapter

Item id: `workflow-controller-h7`.

## Roadmap row (moved verbatim)

Complexity: medium ([#182](https://github.com/RodrigoFAbreu/workflow-controller/issues/182)). Build verification declared by the adapter: each harness adapter names how a build is verified (Claude Code: `verify-claude`; Codex: `verify-codex` once C10g-2 ships, and the Codex reviewer, now pinned to `codex-cli 0.163.0`), so the pinned version in the registry becomes informational and no harness is tied to one version (`HarnessAdapter.build_verification` is `verify-claude` for `claude-code`, where it admits the functional-tester launch only, and `None` for `codex`, which keeps its fail-closed launch checks until C10g-2 ships `verify-codex`; the generated table shows "Captured with" and "Build verification"; nothing at launch changed; a `chore:` change with no release; [#182](https://github.com/RodrigoFAbreu/workflow-controller/issues/182)) (owner decision, 2026-10-24)

Needs: C10e, H1

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
