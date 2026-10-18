# C10b: the pull request defect loop (`workflow-controller-pr-defect-loop`)

This is the record of milestone `workflow-controller-pr-defect-loop` (`docs/ROADMAP.md` step C10b, the second of the
three gate-policy steps), archived when it was accepted on 2026-10-18. Plan:
`docs/ai-workflow/CONTROLLER_PR_DEFECT_LOOP_PLAN.md`, revision 5, approved by policy at `751a968`; base commit
`164d813`. Pull request #92, title `feat: report the pull request's state to the Workflow and apply its review result
so acceptance clears itself`. The sections up to "Known limitations (out of scope)" and the functional review round
are the active-milestone page as it stood during the functional review; the later sections were added at acceptance.

## Scope

Under an automatic acceptance gate the Controller used to stop wherever the Workflow needed a pull-request fact or a
pull-request defect applied: row 38f (a refused acceptance stored a fact whose checks were pending), row 38g (the gate
requires an approved pull request) and row 38d (`pr.apply_review`, which the Controller blocked as unlaunchable).
C10b launches the Workflow's `/apply-pr-review` and runs the Workflow's one fixed `gh pr list` query itself, handing
the verbatim result to `record-external-result --kind pr_review_result` (the `pr_report` step). Pending checks wait
only when they are the one thing awaited, and an unchanged result is not reported twice. A red pull request found
after acceptance, in the merge flow, is left to C10d; row 38e (functional evidence) is left to C10c.

## Implementation

Plan revision 5 is approved (`751a968`). Implementation is complete (CP1-CP4) and technical approval is recorded
(`2ee65f4`, implementation revision 4).
- **CP1 complete** (`712b6d5`): `pr.apply_review` is launched on the new `apply-pr-review` routing role (Opus,
  `xhigh`); a `user_only` worker, a wrong role, a changed invocation or a missing work item is blocked with its code.
- **CP2 complete** (`95729f1`): the `pr_report` step reports the pull request's state to the Workflow at the external
  gates (rows 38f and 38g) through a vetted `gh`, with the wait for pending checks bounded by `merge.wait_seconds`.
- **CP3 complete** (`9caf94d`): the defect loop shown end to end in lifecycle scenarios on the vendored Workflow, and
  `inspect`/`explain` say what the next step does.
- **CP4 complete** (`4bde66b`): notifications pinned (`step_done`, `user_gate`, `lane_blocked`), and the guides, ADR
  0016 and the roadmap (C10b reworded, C10d added) brought in line.
- **Functional fix** (`15b3485`, a bounded documentation fix): the pull request wait was documented as a `--wait`
  option that `run` and `step` do not have; it belongs to `run`, bounded by `merge.wait_seconds`.

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
| 10 | Wait budget. Run (not `step`, which never waits) with a short `merge.wait_seconds` against pending checks in a scenario. | One deadline across gate changes; ends with a stop text naming the pending checks ("no checks reported" for an empty rollup). Ctrl-C ends the wait cleanly. |
| 11 | Documents. Read `docs/guide/runtime.md`, `automation.md`, `commands.md`, `troubleshooting.md`, `docs/run.md`, `docs/compatibility.md`, ADR 0016 and the ROADMAP C10b/C10d rows. | They describe what the Controller does now; no "until C10b" sentence; C10d is a named follow-up; follow-up 26 done except row 38e. |
| 12 | Optional: re-run lifecycle scenarios against the installed Workflow 2.13.0 scripts if importable. | Same results; record any difference as a finding. |

### Known limitations (out of scope)

- A red pull request found **after** acceptance (merge flow) is not looped; that is C10d.
- Row 38e (functional evidence) is still a stop; C10c owns it.
- A real `/apply-pr-review` worker and a real GitHub query are not exercised; fakes only.
- The two Git 2.56 trailer tests and flake #82 fail locally and are unrelated.

### Functional review round 1 (applied)

- Finding 1 (documentation): the pull request wait was described as a `--wait` option that `run`/`step` do not have.
  Fixed in `docs/guide/automation.md`, `docs/adr/0016-pull-request-report-and-apply.md`, `docs/run.md` and flow 10
  above: the wait belongs to `run`, bounded by `merge.wait_seconds`/`merge.poll_seconds`; `step` never waits.
  No code change; no broad finding, so no remediation child.
- Re-test: flow 11 (documents agree) and flow 10 wording. Flow 9 stays BLOCKED by sandbox sockets in the tester's
  environment; the notification-wiring tests need a run outside it.

## Acceptance

Accepted **by gate policy, not by a person**, on 2026-10-18: `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`). Every checkpoint (CP1-CP4) is `COMPLETE`, the
technical approval `2ee65f4` (implementation revision 4) is `CURRENT`, flows flow-1 to flow-12 passed, and the
Workflow's own GitHub query found pull request #92 at head `0ba7ddc` with every check passing and no standing
objection. The pull request title is `feat:`, so it releases the minor release 1.15.0 when it merges. No
`docs/releases/1.15.0.md` was written by the milestone; the release notes come in the docs pull request after the
release.

## Review history

- **Plan.** Revision 5 was approved by policy at `751a968` after two Codex plan rounds and five local plan rounds.
  Codex round 1 `REVISE`: pending checks must not hide an approval in a repository without CI or a
  `CHANGES_REQUESTED` objection. A local round on revision 4 then found that an unchanged report re-armed row 38h and
  a refused `/satisfy-gate acceptance`; revision 5 answered it. Codex round 2 `APPROVE`.
- **Round caps.** The owner set this milestone's review rule on 2026-10-18: a technical round cap of 10, with the
  orchestrator stopping for the owner at 6 local or 4 external rounds per stage (the two-cap rule of issue #88,
  applied by hand). The plan and technical approvals were recorded by Haiku 5.5 workers (owner decision 2026-10-18:
  Haiku for plan and technical approval, Sonnet for acceptance, issue #91).
- **Implementation.** Technical approval `f1c7e28` (implementation revision 3) after two Codex implementation rounds.
  Round 1 `REVISE`: the reporter ran an unvetted `gh` (a repository could fake an approval), non-`applier` roles could
  launch `/apply-pr-review`, and CI-only waits sent `user_gate`. A local round on revision 2 asked for the production
  vetting to be tested. Round 2 `APPROVE`. After the functional fix (the technical approval marked `STALE` at
  `21bce09`, implementation revision 4) a local round approved, and Codex round 3 `REVISE` asked to change the
  approved plan's `--wait` wording too. The orchestrator held it, the owner chose a re-grade with the repository's
  rule that approved plans are historical records, and Codex withdrew the finding and `APPROVE`d (verdicts archived as
  `external-implementation-review-rev4-REVISE-try1-disputed.md` and `external-implementation-review-rev4-APPROVE.md`).
  Technical approval `2ee65f4` (revision 4). Implementation-stage rounds: 4 local and 4 external, including the
  disputed one.
- **Functional.** Round 1, an independent Codex tester with fakes only: flows 1-8, 10 and 12 passed (flow 12 repeated
  the nine lifecycle scenarios on the installed Workflow 2.13.0 with no difference); flow 9 was blocked by sandbox
  sockets; flow 11 found the pull request wait documented as a nonexistent `--wait` option. Report:
  `.ai-review/archive/workflow-controller-pr-defect-loop/functional-tester-round1-RESULTS.md`. Round 2, an operator
  re-test outside the sandbox (`functional-review-round2-PASSED.md`): 117 notification and reporter tests pass, the
  documentation fix is checked, and the whole suite ran 4,256 tests with exact coverage, failing only the two known
  Git 2.56 trailer tests. Checklist evidence commit `0ba7ddc`.

## Minor review findings

Every Optional finding of the milestone's reviews, with its disposition (owner rule, 2026-10-16: acceptance never
waits for a fix). Of sixteen findings, seven were fixed (rows 1, 2, 3, 4, 6, 11 and the functional finding, row 16),
four dropped (rows 5, 8, 9, 15) and five kept as follow-ups (rows 7, 10, 12, 13, 14), in three GitHub issues: #93,
#94 and #95. Follow-ups are `review-follow-up` issues and are worked off in clean-up milestones (CU2 next);
acceptance never waits for them. Sources are under `.ai-review/archive/workflow-controller-pr-defect-loop/`. The
external implementation verdicts were not triaged, and the gitignored `.ai-review/current/*` bundle was absent, so
rows 8 and 9 rest on the verdict text.

| # | Source | Finding | Disposition | Justification |
| --- | --- | --- | --- | --- |
| 1 | plan rev3 O1 (local + external) | The wait must be factored so one budget, `on_wait`, the clock/sleep seams and `WaitInterrupted` are shared, and budget expiry needs a test | fixed | `poll_waitable` at controller/milestone_branch.py:2838 is shared with `waiting_preflight` (:2880); budget-expiry test `tests/test_pr_report.py:379` (sleeps `[10, 10, 5]`); commit 95729f1 |
| 2 | plan rev3 O2 (local + external) | Name the Controller-side anchor read and test it against the Workflow's `anchor_of`, fallback included | fixed | `anchor_commit` at controller/pr_report.py:66; agreement test `tests/test_pr_report.py:151` (with and without the approval, plus the none case); commit 95729f1 |
| 3 | plan rev3 O3 (local + external) | The binding accessor is `record["repository"]["forge_repository"]`, not `_forge_repository_of` | fixed | The reporter uses `binding["repository"]["forge_repository"]` at controller/job.py:7035; commit 95729f1 |
| 4 | plan rev3 O4 (local + external) | CP4 must reword C10b's "merge flow of C4" row and add C10d | fixed | docs/ROADMAP.md:82 (C10b) and :84 (C10d); no "merge flow of C4" text left; commit 4bde66b |
| 5 | plan rev5 O1 | D5 repeats the sentence "explain words that stop as 'reported; the report changed nothing', never as 'not reported'" | dropped | Wording of the approved plan (still twice at docs/ai-workflow/CONTROLLER_PR_DEFECT_LOOP_PLAN.md:32); approved plans are not edited, and the guides say it once (docs/guide/automation.md:373) |
| 6 | impl rev1 #1 | docs/guide/automation.md omits `pr_checks_pending` from the stops that ask a person | fixed | The paragraph now says a step that did not wait ends on `pr_checks_pending` with no notification and a spent wait budget sends `lane_blocked` (docs/guide/automation.md:388-390); behaviour changed too, commit 4301fb5 |
| 7 | impl rev1 #2 | `pending_checks` tests for `ci_green` by a plain substring over the whole stop text, not the `"ci_green:"` token | follow-up | controller/pr_report.py:175 is unchanged; the current Workflow text cannot trigger it, so no defect today (#93) |
| 8 | impl rev1 #3 | The bundle's Risks section / checklist should record that scripts/ is the 2.13.0 install instead of "not done" | dropped | Concerns a gitignored review-bundle text (`.ai-review/current/*`, regenerated per milestone); the identity with the vendored modules is already covered by `VendoredAgreementTest` (tests/test_pr_report.py:89) |
| 9 | impl rev2 #1 | `TEST_RESULTS.md` in the rev-2 bundle was still the rev-1 file | dropped | Gitignored bundle file, regenerated at the next generation (rev 3 was produced at dc4db12); `.ai-review/current` is absent at HEAD so I could not re-read it |
| 10 | impl rev2 #2 | `ReporterExecutableTest` lacks a world-writable directory case, a world-writable executable case and a second-worktree case | follow-up (in part fixed) | World-writable directory is fixed: tests/test_pr_report.py:510, commit 021186e. No test makes the `gh` file itself world-writable (controller/forge.py:249 checks it). The second-worktree case is dropped, see row 15 (#94) |
| 11 | impl rev2 #3 | Test classes sit after the `if __name__ == "__main__"` guard | fixed | The guard is the last statement, tests/test_pr_report.py:605; commit 021186e |
| 12 | impl rev2 #4 | `Context.forge` spots the real forge with `getattr(forge, "vet_root", False) is None` | follow-up | Still there at controller/milestone_branch.py:293. It builds a `GhForge` itself, so the `getattr` test is always true and misleading; pass `vet_root` in or set it directly (#95) |
| 13 | impl rev3 #1 | The world-writable-directory test asserts only `ForgeUndecidableError`, not which rule raised it | follow-up | tests/test_pr_report.py:510-521 still has a bare `assertRaises`; the message at controller/forge.py:250 contains "world-writable" (#94) |
| 14 | impl rev3 #2 | Stray blank lines in `ReporterExecutableTest` and one blank before the final guard (cosmetic) | follow-up | Still true: three blank lines before the class (tests/test_pr_report.py:473-474), two inside it near :526, one before the guard at :604; cosmetic, folded into #94 |
| 15 | impl rev3 #3 | The second-worktree refusal rule of `vet_gh` has no test | dropped | The reviewer accepted the implementer's reason: the same `_is_under` check (controller/forge.py:247) covers the repository root, and `test_a_gh_inside_the_repository...` exercises it |
| 16 | Functional review round 1, finding 1 (Important, documentation) | The pull request wait was documented as a `--wait` option that `run`/`step` do not have | fixed | 15b3485 (automation guide, ADR 0016, `docs/run.md`, the checklist), a bounded functional fix; the approved plan keeps its wording as a historical record (Codex withdrew that point on re-grade) |
