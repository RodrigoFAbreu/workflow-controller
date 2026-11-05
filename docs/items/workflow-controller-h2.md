# H2: Claude as an external reviewer without lowering the policy

Item id: `workflow-controller-h2`.

## Roadmap row (moved verbatim)

Complexity: low to medium ([#185](https://github.com/RodrigoFAbreu/workflow-controller/issues/185)). Claude as an external reviewer without lowering the policy: `reviewer.harness` accepts `claude`, and a cross-model review uses the provider that is not the worker's (`review_mode.worker_families` derives the workers' provider family from the routing, replacing the `WORKER_FAMILY` constant, and a reviewer is cross-model when its family is not in that set; mixed worker families stop the review with `review_mode_unavailable`; with today's registry every outcome is unchanged and `claude` still reads as unset; a `chore:` change with no release; [#185](https://github.com/RodrigoFAbreu/workflow-controller/issues/185))

Needs: H1

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
