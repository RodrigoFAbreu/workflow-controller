# RA1: Scheduled retention alert after each finished work item

Item id: `workflow-controller-ra1`.

Not yet planned. After each finished work item the Controller runs the Manager's read-only retention check and sends a SignalHub info event when a retention threshold is reached, when items are archived and when items are deleted. Complexity: small to medium.

## Owner decision (2026-11-14)

The Controller owns the scheduled check and the notification. The Manager owns the retention rule and its evaluation, and will add a read-only `--json` form to `workflow-manager doctor --retention <repo>`.

The `--json` output carries these fields:

- `repo`: the repository checked.
- `rule`: the retention rule that was evaluated.
- `threshold`: the limit of the rule.
- `current_value`: the measured value.
- `reached`: whether the threshold is reached.
- `state`: one of `reached`, `archived`, `deleted` or `clear`.
- `alert_key`: a stable key for the alert.

## Wanted

- Trigger: after each finished work item (a merge or a release), the Controller runs `workflow-manager doctor --retention <repo> --json`. There is no daily timer.
- Notifications, each sent as a SignalHub event:
  - `info` with `low` priority when a threshold is reached;
  - `info` with `low` priority when items are archived;
  - `info` with `medium` priority when items are deleted.
- One SignalHub event per `alert_key`. No repeat is sent until `current_value` changes or the state returns to `clear`.
- A `clear` state sends nothing, and closes the alert so a later reach is reported again.

## Depends on

The Manager's `--json` form of `doctor --retention` must exist first. This item does not change the retention rule or its evaluation. Retention of finished work-item files itself is [CL1a](workflow-controller-cl1a.md).

## Open design points

- Where the Controller records the last `alert_key` and value it sent.
- How this works with the existing `notifications.*` settings.
- What the Controller does when the Manager is older and has no `--json` form: skip silently or report once.

## Sizing note

One mechanism: one check and one deduplicated event per alert key, after a finished work item.
