# H8: Supported models per harness

Item id: `workflow-controller-h8`.

## Roadmap row (moved verbatim)

Complexity: medium. Supported models per harness: each adapter declares its known models and effort levels, the routing of H1 checks the model and effort against the chosen harness (a Claude model routed to Codex is refused at load, not at launch), and a settings entry adds a model without a release (`claude-code` declares its seven model names and five efforts, `codex` declares `gpt-6.1-sol` with `high` and `xhigh`; `routing.check_route_values` runs on the resolved routes of `RoutingOptions` and refuses an unsupported pair with exit 20 before any job record; the `harness.models` setting, table generation 16, adds a model; the harness page shows the declared `Models` and `Efforts`; "Upgrading to 1.22.0" in the compatibility page states the newly refused configurations) (owner decision, 2026-10-24)

Needs: H1

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
