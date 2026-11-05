# H4b2: Codex worker, step 2b

Item id: `workflow-controller-h4b2`.

## Roadmap row (moved verbatim)

(a `chore:` change, no release; `controller/codex_worker.py`, registered for a fixture role only). Complexity: medium ([#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255)). Codex worker, step 2b: a headless one-shot `codex exec` launch in the job's worktree behind the H3 seam, on a build verified by `verify-codex` (C10g-2b), under the H4b1 sandbox; a fixture role only. It adds the worker's composed-configuration check and refuses a nested `.git` entry in the worktree after the run (both open limits of H4b1), and compares `guard_digest` around every run

Needs: H4b1

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
