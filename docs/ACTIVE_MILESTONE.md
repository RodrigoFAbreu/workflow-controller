# Active Milestone

## Status

**Complete.** `workflow-controller-worker-lifecycle-ownership` reached `MILESTONE_COMPLETE` on
2026-09-26. Functional review passed in round 3 (implementation revision 4) with no findings,
against checklist evidence commit `272bcb36d60551d8ae8ca85cc48d255e51a63d79` (checklist blob
`71aa72372087e1987cac7849735e6477bd100299`). Flows F1-F11 all passed on a fresh, isolated pipx
install of a wheel built from that commit (`workflow-controller 1.1.1`, `runtime: package (local
build from 272bcb36d605)`). F12, the optional live `claude` harness-contract probe, was skipped.
The three earlier findings were re-tested and did not reproduce:
- round 1's F1: the wakeup due time `15:56:09` matched the persisted `14:56:09.146Z`;
- round 1's F2: every line used `process`/`processes`, and `processs` never appeared;
- round 2's F3: F11 named the daemon `not owned: pid <d> (gpg-agent 600)`, and F5's excluded
  entry carried `cmdline: 'gpg-agent 600'`.

All eight registry checkpoints (`CP1`-`CP8`) are `COMPLETE`. Implementation revision 4 was
approved by both implementation-review stages (technical approval `4328131`, `CURRENT`), and the user accepted
the milestone through `/accept-milestone`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md`
marks the section 1.4 hotfix accepted. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-worker-lifecycle-ownership.md`. It covers the goal,
checkpoint progress, the self-review, implementation review round 1, functional review rounds 1
and 2, and the revision-4 functional-review checklist, including its driver.

This milestone's own deliverables remain live in the tree, unmoved (see the archive file's own
preface for why): `docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md` and its
registry/mapping files, still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry
declares, and ADR `docs/adr/0004-worker-lifecycle-ownership.md`. The previously completed work
items and their archived narratives in `docs/milestones/completed/` are unaffected.

Deferred follow-ups, not conditions of acceptance:
- with no Controller attached, a job whose worker leader died while its group still runs reads
  `waiting` from the last recorded `worker_state` (functional review round 1, O1; wording only);
- `follow` stamps worker stream lines that carry no event time with the time they are read, and a
  present but unreadable generic event time still renders as the current time (the checklist's
  known limitation);
- two messages still use the neutral `process(es)`: the drain stderr line and the `resume
  --abandon` refusal;
- in F8's scenario, a subagent's own `assistant` events that arrive while the session is idle
  count as an extra turn (`stream_diagnosis.turns` is `3`, not `2`). The outcome is unaffected
  (checklist dry-run notes);
- implementation review round 1's Optional 2 (declining `ENDING` once the anchor has died) and
  Optional 3 (`exit_status` wording in plan B's table) were not applied;
- what ADR 0004 and the README's "What is not solved here" leave unsolved by design: reliance on
  the measured harness behaviour, wakeup-fire matching by an undocumented event, an `env -i`
  descendant orphaned while nothing supervises it, and daemon recognition by name;
- carried over, unchanged: `docs/ROADMAP.md` section 1.4's four follow-up patches (the misordered
  `--work-item` resume hints, the manual-external gate's behaviour when the local review ledger is
  incoherent, the abandoned/unreconcilable apply-review relaunch-bound tests, active-job/status
  presentation), and the deferred items of the earlier milestones, listed in their acceptance
  commits (`4280bb6`, `82fa6a8`).

Supervised rollout, still outstanding: the first automatic release, 1.2.0 (README "Runbook: the
first automatic release"), has not run yet. Nothing of this milestone or the previous one has been
pushed.

**Next action:** `docs/ROADMAP.md` section 1.4, "Follow-up patches to fold in where appropriate",
is the next incomplete milestone that can be planned now. Its four patches are still open. Run
`/milestone-plan` for it. That creates a fresh `work_items` entry and claims
`active_work_item_id`, ready for `PLANNING`. Section 1.6, "Post-Workflow-2.6 compatibility
integration", waits for Workflow Manager's 2.6.x release; plan it once that release is installed
through Workflow Manager. Independently of planning, the supervised 1.2.0 release above is an
operator task, not a milestone.
