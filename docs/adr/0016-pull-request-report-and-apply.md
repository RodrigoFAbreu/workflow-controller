# ADR 0016: Report the pull request's state to the Workflow, and launch its review-result apply

Status: accepted (2026-10-18). Builds on
[ADR 0015](0015-gate-policy-validations-and-open-enums.md) (the Controller launches
the Workflow's validations) and [ADR 0013](0013-review-only-harness-seam.md) (a
Controller-run step hands its result to the Workflow with `record-external-result`).
The code is `controller/pr_report.py`, `controller/job.py` and
`controller/protocol_decision.py`; the operator's view is
[The pull request defect loop](../guide/automation.md#protocol-mode-workflow-27-and-later).

## Context

Under an automatic acceptance gate the Controller still stopped at three
Workflow rows for pull request facts: 38f and 38g (`pr.review.external`, the
Workflow wants a fact or an approval) and 38d (`pr.apply_review`, a reported
fact or an unapplied cause arms the Workflow's own query). `next-action` never
runs `gh`, so after a refused `/satisfy-gate acceptance` stored a fact whose
checks were pending, the answer stayed 38f after the checks turned green until a
person acted.

## Decision

1. **The Workflow decides; the Controller launches or reports.** `pr.apply_review`
   is launched like every automatic action, as `/apply-pr-review <id>` on a role
   of its own, `apply-pr-review` (Opus, `xhigh`, not single-agent; settings table
   generation 10). The Controller reads no fact, evaluates no cause and chooses
   no branch: that is the command's own cause table.
2. **The reporter is a Controller step, not a worker, and reports only what
   GitHub says.** At rows 38f and 38g under an **automatic** acceptance gate, for
   a work item with a milestone-branch binding and a pull request, the Controller
   runs the Workflow's one fixed `gh pr list` query for the item's anchor commit
   and records the verbatim output with `record-external-result --kind
   pr_review_result`. It parses nothing; the Workflow parses `raw`, and a reported
   fact only arms the query. Under a human acceptance gate it does nothing.
3. **A report changes the answer, or it is not made.** A result whose
   `raw_sha256` equals the last report's is not sent again: the step stops
   `pr_report_unchanged`. Such a report still costs one refused
   `/satisfy-gate acceptance` (row 38h) because the stored fact is now older; the
   plan accepts that one bounded launch rather than parse the fact in the
   Controller.
4. **Pending checks wait only when they are the one thing awaited.** The step
   waits (`pr_checks_pending`, polled by the shared bounded wait that
   `--wait` uses) only at row 38f, only when the Workflow's own stop text names
   `ci_green`, and only when the record carries no standing objection. At row
   38g, or with a `CHANGES_REQUESTED` decision or a failing check, pending
   checks are reported like any other result, so they never hide an approval or
   an objection. The one hand-written predicate mirrors the Workflow's
   `parse_forge_raw`, and a test over the vendored tree ties the two together.
5. **A stop asks for a person only when one is needed.** A reported result is a
   `step_done`; a stop that needs a person (an approval missing at 38g, a human
   gate, the Controller's own `pr_report_*` gates, row 38e) is a `user_gate`; a
   stop the Workflow calls `blocked` is a `lane_blocked`. Pending checks follow the
   wait semantics of the branch gates: a step that did not wait sends nothing, and
   one whose wait budget ran out sends `lane_blocked`; they never ask for a person.

## Consequences

- A run whose acceptance was refused for pending checks carries on once they are
  green, and a pull request found red **before** acceptance reopens the same work
  item into remediation and returns to acceptance, with no person.
- A red pull request found **after** acceptance, in the merge flow, still ends at
  that flow's `checks_failing` stop. Reaching row 38d for a completed item needs
  the merge flow to report the fixed query; it is the roadmap step C10d.
- The functional evidence of row 38e is still a stop, owned by C10c.
- Tests run on the vendored Workflow answers with an injected `gh` runner; none
  reaches GitHub.
