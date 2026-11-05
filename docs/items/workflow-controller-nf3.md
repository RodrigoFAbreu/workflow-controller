# NF3: Notifications: user_gate only for a stop that needs a person

Item id: `workflow-controller-nf3`.

## Roadmap row (moved verbatim)

(a `fix:` change, a patch release). Complexity: low ([#244](https://github.com/RodrigoFAbreu/workflow-controller/issues/244)). Notifications (a `fix:` patch): `user_gate` (ACTION_REQUIRED) is sent only for a stop that needs a person; the stops the orchestrator handles (`no_progress_repeated`, an external gate waiting for a pull request, `gate_evidence_unmet` and the like) get an event of their own at a lower severity, as `review_wait` and `checks_wait` do (18 of the 20 `user_gate` events since 2026-10-26 were false alarms)

Needs: NF2

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
