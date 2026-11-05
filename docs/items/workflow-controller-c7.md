# C7: A review-only harness seam with a Codex reviewer

Item id: `workflow-controller-c7`.

## Roadmap row (moved verbatim)

A review-only harness seam with a Codex reviewer: the Controller runs the cross-model review itself, and a Codex worker it starts passes the usage gate (`usage.admit`, `usage.complete`) (released as 1.10.0, PR #45, squash `6aa8f66`; accepted 2026-10-15 by gate policy; ADR 0013; C10 reuses the seam, the supervision precedent and `record_external_result`)

Needs: C8

Section: the smallest slice of [5](../roadmap-archive.md#5-harness--agent-portability)
