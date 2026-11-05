# H4b1: Codex worker, step 2a

Item id: `workflow-controller-h4b1`.

## Roadmap row (moved verbatim)

(a `chore:` change, no release; `controller/codex_worker_sandbox.py`, nothing calls it). Complexity: medium ([#252](https://github.com/RodrigoFAbreu/workflow-controller/issues/252)). Codex worker, step 2a: the worker's writable-worktree sandbox (the `worker` permissions profile, the pinned `codex exec` line, the build), its boundary probes and the `.git` guard digest, proved live on `codex-cli 0.163.0` (`tests/harness_contract/codex/worker_sandbox.txt`; a nested read-only `.git/hooks` and `.git/config` held, and `.git` needs an explicit `write` entry)

Needs: H4a, C10g-2b

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
