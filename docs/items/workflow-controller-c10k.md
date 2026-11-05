# C10k: `runtime.remove_tree` refuses a path whose parent chain holds a symbolic link

Item id: `workflow-controller-c10k`.

## Roadmap row (moved verbatim)

(a `fix:` change, a patch release, 1.27.9; no new setting). Complexity: low. `runtime.remove_tree` refuses a path whose parent chain holds a symbolic link (the first point of [#140](https://github.com/RodrigoFAbreu/workflow-controller/issues/140); split out of CU3: it is a production primitive, not a test)

Needs: C10f

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
