# CL1d: A notification

Item id: `workflow-controller-cl1d`.

## Roadmap row (moved verbatim)

Complexity: low to medium. A notification (SignalHub) once when the limit is crossed

Needs: CL1b

Section: [Clean-up milestones](../roadmap-archive.md#at-a-glance)

## Planning notes

The issue [#130](https://github.com/RodrigoFAbreu/workflow-controller/issues/130) asks for the rule, the check, the command and working links; it has no step for a notification, so this item comes from the row alone. The limit is the one in CL1b's check, on which it depends.

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Owner decisions 2026-11-14

- **Ownership.** The Controller owns the scheduled check and the SignalHub notification. The Manager owns the retention rule and its evaluation: the per-repository policy in `.workflow-manager/retention-policy.json` (mode `notify`; thresholds `finished_items` 30, `finished_folder_mib` 200 and `finished_max_age_days`). `workflow-manager doctor --retention <repo>` prints a text `retention alert:` line today; the Manager will add a read-only `--json` form of it.
- **The `--json` form.** One record per repository and rule with the fields `repo`, `rule` (`finished_items`, `finished_folder_mib` or `finished_max_age_days`), `threshold`, `current_value`, `reached`, `state` (`reached`, `archived`, `deleted` or `clear`) and a stable `alert_key` per repository, rule and state.
- **Trigger.** The check runs after each finished work item (a merge or a release). There is no daily timer.
- **Notifications.** Info priority low when a threshold is reached; info priority low when items are archived; info priority medium when items are deleted.
- **No repeats.** One SignalHub event per `alert_key`. It is not sent again until the value changes or the state returns to `clear`.
- **Dependency.** The Manager's `--json` form of `doctor --retention`. This is a dependency in prose only; it is not a backlog item.

### Relationship to the Manager's retention policy

CL1a to CL1c describe a rule, a `verify` check and a prune command owned by the Workflow and followed by the Manager and the Controller. The Manager's per-repository retention policy already evaluates thresholds and can archive or delete, so it overlaps CL1b (the limit) and CL1c (pruning). For this item the limit is the Manager's evaluation, read through its `--json` form, rather than a second one computed from CL1b's check; the `Needs: CL1b` line is kept as the backlog ordering, not as a second source of the limit. Which side performs the archive and the delete (CL1c's command or the Manager's policy) is not settled here: this item only reports the states `archived` and `deleted` that the Manager produces, and it does not prune. That question should be settled when CL1b and CL1c are planned.
