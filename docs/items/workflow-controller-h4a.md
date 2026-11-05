# H4a: Codex worker, step 1

Item id: `workflow-controller-h4a`.

## Roadmap row (moved verbatim)

(a `chore:` change, no release; `controller/command_files.py`, nothing calls it). Complexity: medium ([#242](https://github.com/RodrigoFAbreu/workflow-controller/issues/242)). Codex worker, step 1: render a role's command file for Codex from the shared command files (`docs/ai-workflow/COMMAND_FILES.md`) and build its prompt, with no per-harness copies of the files; the adapter refuses the user-only commands (`/approve-review`, `/accept-milestone`, `/request-plan-amendment`); no launch (the shared-command-file contract shipped in Workflow 2.18.0, [workflow#59](https://github.com/RodrigoFAbreu/workflow/issues/59))

Needs: H3

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
