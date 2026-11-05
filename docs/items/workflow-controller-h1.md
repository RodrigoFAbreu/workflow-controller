# H1: Routing names a harness

Item id: `workflow-controller-h1`.

## Roadmap row (moved verbatim)

Complexity: medium. Routing names a harness: a routing entry (the `routing` section, `--routing-config`, a new `--role-harness ROLE=HARNESS`) gains `harness`, checked against the roles the adapter declares; an undeclared role is a named refusal and the default stays `claude-code`; no new launch code. First item of the harness-neutral roles epic (owner decision, 2026-10-24) (a routing entry, a `--routing-config` file and `--role-harness` name a harness, accepted for `claude-code` on every routing role and for `codex` on none yet; settings table generation 15; `worker_route.harness` is the adapter name for every job kind; [#158](https://github.com/RodrigoFAbreu/workflow-controller/issues/158))

Needs: A1

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
