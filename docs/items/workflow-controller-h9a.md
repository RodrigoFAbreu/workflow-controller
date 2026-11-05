# H9a: The chain of a fallback per role, the shape only

Item id: `workflow-controller-h9a`.

## Roadmap row (moved verbatim)

Complexity: medium ([#197](https://github.com/RodrigoFAbreu/workflow-controller/issues/197)). The chain of a fallback per role, the shape only (a role entry may list further `{harness, model, effort}` routes under `fallback`, in a `--routing-config` file and the settings file's `routing` section; every entry is validated at load like the first route, `RoutingOptions.chain(role)` returns the whole chain, the worker families and the mixed-lane reason count every entry, and the settings table generation is 17; nothing selects a later entry yet; a `chore:` change with no release)

Needs: H1, H3

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
