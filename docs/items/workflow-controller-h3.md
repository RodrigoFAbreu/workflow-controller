# H3: The worker launch behind the adapter

Item id: `workflow-controller-h3`.

## Roadmap row (moved verbatim)

Complexity: medium ([#191](https://github.com/RodrigoFAbreu/workflow-controller/issues/191)). The worker launch behind the adapter: one `launch` interface that Claude Code implements, with no change in behaviour (the existing tests unchanged), so a second harness can plug in (A1 registered the declarations and left the launch code where it was; this is the missing launch part) (`controller/worker_launch.py` holds the `WorkerLauncher` interface, `ClaudeCodeLauncher` (which calls `worker.launch` unchanged) and the registry `LAUNCHERS`; `controller/job.py` resolves the launcher from the routed harness before the usage gate; a `chore:` change with no release; [#191](https://github.com/RodrigoFAbreu/workflow-controller/issues/191))

Needs: H1

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
