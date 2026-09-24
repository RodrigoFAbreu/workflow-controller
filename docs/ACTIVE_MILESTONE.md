# Active Milestone

## Status

**Complete.** `workflow-controller-release-runtime-observability` reached `MILESTONE_COMPLETE` on
2026-09-24. Functional review passed in round 1, against checklist evidence commit
`c1272630517120cc1a94793f0c6d1ff76b14ec9b`. Flows 1-8 passed on a fresh, isolated pipx install.
The two extra checks also passed: waiting for the worker's process group, under both the `/proc`
scan and the `killpg` fallback, and the `Follow it:` hint on a lock refusal and on `resume`. The
optional live flow 9 was waived: the no-cost flows covered the packaged runtime and the whole
`follow` surface, and CP11 step 6 had already run the live stream.

All eleven registry checkpoints (`CP1`-`CP11`) are `COMPLETE`. Implementation revision 1 was
approved by both implementation-review stages (technical approval `0e8cecb`), and the user
accepted the milestone through `/accept-milestone`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, and `active_work_item_id` is now `null`.
`docs/ROADMAP.md` marks sections 1.1-1.3 complete; this acceptance commit is the first to track
it. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-release-runtime-observability.md`. It covers the
goal, checkpoint progress, self-review and functional-review checklist.

This milestone's own deliverables remain live in the tree, unmoved (see the archive file's own
preface for why): `docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md` and its
registry/mapping files, still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry
declares. The previously completed work items and their archived narratives in
`docs/milestones/completed/` are unaffected.

Deferred follow-ups, not conditions of acceptance:
- manual external implementation review, optional O1: where the drain cannot count the process
  group's members, `status`, the follower heartbeat and the rendered `worker_exited` line say
  `0 process(es) at drain start ()`. The stderr drain line correctly says `an unknown number of
  processes`. Reproduced in the functional review under the `killpg` fallback;
- `rate_limit_event` renders as `worker unknown event rate_limit_event` (self-review M2);
- replayed worker-stream lines carry the time of the replay, not of the event, since `stream-json`
  events have no timestamp. A `--from-start` replay prints all of a job's lifecycle lines before
  its worker lines (functional review P1, C3);
- checklist wording: flow 8 omits the `python3 tools/release.py` prefix on one `verify-wheel`
  line, and the checklist has no cleanup step (functional review C1, C2);
- the README's upgrade command, `pipx install --force`, fails on a pipx with the `uv` backend
  (`A virtual environment already exists`). `pipx uninstall` and then `pipx install` works
  (functional review E1);
- carried over from `workflow-controller-automatic-lifecycle-orchestration`: the manual-external
  gate can name a stale ledger `review_content_id` after a failed local-review job; gates'
  `explain --work-item <id>` resume hint does not parse as written; no explicit test pins
  `OperatorAbandoned`/`UnreconcilableJobError` records in the apply relaunch bound.

**Next action:** `docs/ROADMAP.md` section 1.4, "Follow-up patches to fold in where appropriate":
the misordered `--work-item` resume hints, the manual-external gate's behaviour when the local
review ledger is incoherent, the abandoned/unreconcilable apply-review relaunch-bound tests, and
active-job/status presentation (O1 above fits here). Run `/milestone-plan` for it. That creates a
fresh `work_items` entry and claims `active_work_item_id`, ready for `PLANNING`. Section 2,
"Workflow / Workflow Manager Migration Hardening", follows.
