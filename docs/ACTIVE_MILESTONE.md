# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-pr-defect-loop` (`docs/ROADMAP.md` step C10b, the second of the
three gate-policy steps) is at implementation revision 3 with technical approval recorded (`f1c7e28`). All registry
checkpoints (CP1-CP4) are implemented. The plan is `docs/ai-workflow/CONTROLLER_PR_DEFECT_LOOP_PLAN.md` (revision 5),
the decision record is `docs/adr/0016-pull-request-report-and-apply.md`, and `docs/ai-workflow/WORKFLOW_STATE.json`
is the ground truth for the phase. The previous milestone, `workflow-controller-gate-policy`, is archived at
`docs/milestones/completed/workflow-controller-gate-policy.md`.

What changed, in one paragraph: under an automatic acceptance gate the Controller now launches the Workflow's
`/apply-pr-review` (`pr.apply_review`, new role `apply-pr-review`, Opus `xhigh`) and, at the pull-request external
gates (rows 38f and 38g), runs the Workflow's one fixed `gh pr list` query itself and hands the verbatim result to
`record-external-result --kind pr_review_result` (the `pr_report` step). Pending checks wait only when they are the one
thing awaited; an unchanged result is not reported twice.

## Functional review checklist

Findings go to `.ai-review/workflow-controller-pr-defect-loop/feedback/FUNCTIONAL_REVIEW.md`.

**Safety rule for every flow.** Never point a test at the real repository's `origin` or a real `gh`: use a scratch
clone with a scratch bare `origin` (or a `https://github.com/owner/repo.git` URL nothing is pushed to) and a fake `gh`
on a vetted path (not under `/tmp`, not in the repository, not world-writable). No flow below needs GitHub.

### Setup

1. Check out the milestone branch at the evidence commit (reported in the gate message).
2. `python3 -m unittest discover -s tests` is green except the two known Git 2.56 trailer tests
   (`test_release_notes`, `test_pull_request_lifecycle`) and the load flake #82.
3. `python3 tools/check_docs.py` passes; the golden generators pass with `--check` (never run them without it).

### Test data

The scenarios of `tests/test_protocol_lifecycle.py` (the pull-request defect loop class) and `tests/test_pr_report.py`
seed a work item at `AWAITING_FUNCTIONAL_REVIEW` with a milestone-branch binding, a pull request, an injected `gh`
runner and the vendored Workflow 2.12.0 answers. Run them with `-v` to read each scenario's name.

### Flows

| # | Flow | Expected |
|---|------|----------|
| 1 | `pr.apply_review` launch: run `tests.test_protocol_decision` and `tests.test_routing`. Inspect `inspect`/`explain` text for a row-38d answer. | `/apply-pr-review <id>` on role `apply-pr-review` (Opus, `xhigh`); a `user_only` worker, wrong role, changed invocation or missing work item is blocked with its code; `inspect` no longer calls it unlaunchable. |
| 2 | Settings: `controller` settings table. | Table generation is 10 and `apply-pr-review` is a documented, overridable role (`docs/guide/commands.md`). |
| 3 | Pending clears itself (lifecycle scenario 1). | Refused acceptance stores a pending fact (38f); the step waits (`pr_checks_pending`); checks go green; it reports; 38d; `/apply-pr-review` refreshes (`no_progress`); 38h; `/satisfy-gate acceptance` completes. No `user_gate`. |
| 4 | Red pull request reopens the item (scenario 2 and 6). | `CHANGES_REQUESTED` or failing checks (even with others pending) is reported at once, 38d launches `/apply-pr-review`, the item reopens, the run returns to acceptance and completes. Loop guard does not trip. |
| 5 | Unchanged result does not loop (scenario 3, 4). | The same output is reported once; the next step stops `pr_report_unchanged`; at most one refused `/satisfy-gate acceptance` (row 38h) follows; never a second report of the same output nor a third apply. |
| 6 | Approval needed (scenario 4, 5). | Row 38g unapproved stops with `user_gate` naming the approval (empty or pending checks do NOT hide it); after the fake decision becomes `APPROVED` the next step reports and the run completes. |
| 7 | Boundaries (scenario 7, 8, 9). | No binding: old 38f stop. Human acceptance gate at 38g: nothing reported, `user_gate` with the `/accept-milestone` remedy. Red pull request after acceptance: the merge flow's own `checks_failing` stop, no `pr_report` job, no apply (deferred to C10d). |
| 8 | Reporter refusals (`tests.test_pr_report`). | Non-`github.com` origin ends in a gate naming `forge_unavailable`; no record at the anchor commit stops with the missing-pull-request code; a binding repository differing from `origin` ends in the Workflow's mismatch gate; nothing is written but `record-external-result`. |
| 9 | Notifications (CP4). | `pr_report` `reported` sends `step_done`; stops needing a person send `user_gate`; a Workflow-blocked stop or a spent wait budget sends `lane_blocked`; a step that did not wait sends nothing. Read `docs/guide/automation.md` against this. |
| 10 | Wait budget. Run with a short `--wait` against pending checks in a scenario. | One deadline across gate changes; ends with a stop text naming the pending checks ("no checks reported" for an empty rollup). Ctrl-C ends the wait cleanly. |
| 11 | Documents. Read `docs/guide/runtime.md`, `automation.md`, `commands.md`, `troubleshooting.md`, `docs/run.md`, `docs/compatibility.md`, ADR 0016 and the ROADMAP C10b/C10d rows. | They describe what the Controller does now; no "until C10b" sentence; C10d is a named follow-up; follow-up 26 done except row 38e. |
| 12 | Optional: re-run lifecycle scenarios against the installed Workflow 2.13.0 scripts if importable. | Same results; record any difference as a finding. |

### Known limitations (out of scope)

- A red pull request found **after** acceptance (merge flow) is not looped; that is C10d.
- Row 38e (functional evidence) is still a stop; C10c owns it.
- A real `/apply-pr-review` worker and a real GitHub query are not exercised; fakes only.
- The two Git 2.56 trailer tests and flake #82 fail locally and are unrelated.

## Next action

Test, then put findings in the feedback file and run `/apply-functional-review`, or, if clean, run `/accept-milestone`.
