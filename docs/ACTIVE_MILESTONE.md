# Active Milestone

## Status

**Complete.** `workflow-controller-signalhub-notifications` (`docs/ROADMAP.md` step C5, section 11.4)
reached `MILESTONE_COMPLETE` on 2026-10-12. The Controller is now a SignalHub producer: it sends 14 event
types (progress, human gates, blockers, merges, releases and the usage events of ADR 0011) to SignalHub's
`POST /api/v1/events` from the records it already keeps. Each type is configurable in
`notifications.events` (on/off, category, severity), every send is idempotent and runs off the lifecycle's
thread, delivery never changes a step's outcome, and `workflow-controller notifications [--test]` checks the
wiring. ADR `docs/adr/0012-signalhub-notifications.md` records the design. The milestone ships as the minor
release **1.9.0** when its `feat:` pull request merges.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`). Functional review round 1 found
three Minor issues, fixed by a bounded fix (`5a8233a`, `117449a`). Round 2 (implementation revision 7),
checked against evidence commit `fe70047`, passed every flow (flow-1 to flow-9). An independent tester on
`anthropic/claude-sonnet-5-5` ran the flows.
- Plan revision 16 was approved by policy at `8056cc3`. The base commit is `e0f400e`.
- Technical approval `9db833f` was recorded by policy and is `CURRENT`: review content id `a706b67c`,
  reviewed implementation head `117449a`.
- The Workflow's own GitHub query found pull request #41 at head `fe70047` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, including its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C5 complete. The full milestone
narrative is archived verbatim at `docs/milestones/completed/workflow-controller-signalhub-notifications.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge and release.** Pull request #41, titled `feat: push run events to SignalHub as configurable
  notifications`, is merged once every check passes; `main.yml` then releases 1.9.0.
- **Lane coordination after the release** (ROADMAP 11.4, plan D9). The lane orchestrators stop
  hand-sending the events the Controller now sends, run `resume` before `run --max-steps 1` after a killed
  process, and give every launch path a key (`notifications.api_key_file`, or `SIGNALHUB_API_KEY_FILE` in
  `start-run.sh`).
- **Two Minor observations from functional review round 2** (no change needed; see
  `.ai-review/archive/workflow-controller-signalhub-notifications/functional-review-round2-PASSED.md`):
  `--json` is a global option placed before the subcommand, and a plain `usage` lapses only job-bound
  reservations (an unbound one lapses on the next reserve, check, renew or release).
- **Known limitations kept out of scope:** no persistent outbox, no SignalHub changes, and no
  notifications for the Manager or Workflow repositories.

## Next action

1. Pull request #41 merges (a `feat:` pull request, releasing 1.9.0).
2. `/milestone-plan` for C6, the next step in `docs/ROADMAP.md`'s order (C6, C7, C10, C12, C11, then
   O1-O7).
