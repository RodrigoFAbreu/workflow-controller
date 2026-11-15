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

- **Ownership.** The Controller owns the scheduled check and the SignalHub notification. The Manager owns the retention rule and its evaluation: the per-repository policy in `.workflow-manager/retention-policy.json` (mode `notify`; thresholds `finished_items` 30, `finished_folder_mib` 200 and `finished_max_age_days`). `workflow-manager doctor --retention <repo>` prints a text `retention alert:` line; the Manager has since added a read-only `--json` form of it (see the next subsection).
- **The `--json` form.** Superseded by the interface as released, described under "Manager interface as released" below. The Manager is stateless and has no `archived` or `deleted` state.
- **Trigger.** The check runs after each finished work item (a merge or a release). There is no daily timer.
- **Notifications.** Info priority low when a threshold is reached; info priority low when items are archived; info priority medium when items are deleted. The Controller derives the archived and deleted events itself (see below).
- **No repeats.** One SignalHub event per `alert_key` for a reached alert. It is not sent again until the value changes (the `alert_key` changes with it) or the state returns to `clear`.
- **Dependency.** The Manager's `--json` form of `doctor --retention`, released in Manager 1.20.0. This is a dependency in prose only; it is not a backlog item.

### Manager interface as released (Manager 1.20.0, workflow-manager#220)

`workflow-manager doctor --retention <repo> --json` is read-only. It exits 0 whenever the document prints (the exit-2 refusals before evaluation remain) and prints only the JSON document. It is documented in the Manager's `docs/retention.md`, section "The retention alert as JSON".

Top-level keys:

- `kind`: `retention_alert`.
- `schema_version`: `1`.
- `repo`.
- `policy`: `{state, mode, digest}`.
- `evaluation`: `evaluated`, `not_evaluated` or `not_applicable`. `not_applicable` means no policy, mode `off`, cleanup mode, or a changed policy.
- `not_evaluated`: `null`, or a cause plus `next` text. It is never treated as clear.
- `report_complete`: boolean.
- `alerts`: one per threshold, in this fixed order: `finished_items`, `finished_folder_mib`, `finished_max_age_days`, `archives`, `prunable_archives`.
- `snapshot`: `{finished, with_review_folder, archived_and_removed, finished_folder_bytes, archive_store, prunable_archives, interrupted_removals}`, where `archived_and_removed` is a list of ids.

Each alert has `rule`, `threshold`, `threshold_unit`, `current_value`, `value_unit`, `lower_bound` (true means read the value as "at least"), `unknown_age`, `reached`, `state` (`reached` or `clear` only), `alert_key` and `next` (text). The `alert_key` is an opaque string, `retention:{repo}:{rule}:{state}` with `:{value}` appended for `reached`; compare it whole and never parse it.

Change from the owner's decision above: the Manager has no `archived` or `deleted` state, because it is stateless. The Controller therefore:

- sends info priority low on a reached alert, keyed by `alert_key`, so it repeats only when the value changes;
- derives the archived event (info, low) and the deleted event (info, medium) itself, by keeping the previous JSON document and diffing the snapshots: an id newly present in `archived_and_removed` is archived; a drop in `with_review_folder` or `finished_folder_bytes` with no matching archive is deleted;
- treats `evaluation` `not_evaluated` as an info that the check did not run, never as clear.

The retention commands that `next` names are user-only: the owner runs them, the Controller does not.

### Relationship to the Manager's retention policy

CL1a to CL1c describe a rule, a `verify` check and a prune command owned by the Workflow and followed by the Manager and the Controller. The Manager's per-repository retention policy already evaluates thresholds and can archive or delete, so it overlaps CL1b (the limit) and CL1c (pruning). For this item the limit is the Manager's evaluation, read through its `--json` form, rather than a second one computed from CL1b's check; the `Needs: CL1b` line is kept as the backlog ordering, not as a second source of the limit. Which side performs the archive and the delete (CL1c's command or the Manager's policy) is not settled here: this item only reports the archived and deleted events it derives from the Manager's snapshots, and it does not prune. That question should be settled when CL1b and CL1c are planned.
