# H9b: The selection

Item id: `workflow-controller-h9b`.

## Roadmap row (moved verbatim)

Complexity: medium ([#202](https://github.com/RodrigoFAbreu/workflow-controller/issues/202)). The selection: a fallback chain's later entry is used when the first is unavailable or held by a limit, chosen before a worker is spawned and never mid-job, recorded in the job record and telemetry, with the behaviour when every entry is held (`controller/route_select.py` walks the chain; an entry is skipped when its launcher cannot be resolved or its provider is held, a hold skips every later entry of that provider, and when none can launch the earliest waitable hold or the first entry's error is returned as today; `worker_route.selection` and the telemetry dimensions `chain_position` and `chain_skipped` record it; with the shipped harnesses a chain cannot change the route; a `feat:` change; the tester's fallback of C10g-3 uses it)

Needs: H9a

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
