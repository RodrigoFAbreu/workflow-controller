# CU2: the second clean-up milestone (`workflow-controller-cleanup-2`)

This is the record of milestone `workflow-controller-cleanup-2` (`docs/ROADMAP.md` step CU2), archived when it was
accepted on 2026-10-18. Plan: `docs/ai-workflow/CONTROLLER_CLEANUP_2_PLAN.md`, revision 8, approved by policy at
`b4f3a07`; base commit `4f1e127`. The sections up to "Known limitations" are the active-milestone page as it stood
during the functional review; the later sections were added at acceptance.

## Issues

CU2 fixes twelve GitHub issues labelled `review-follow-up` or `bug`: #78, #79, #82-#87, #91 and #93-#95. Its pull
request (#99, title `feat:`, so the minor release 1.16.0) closes them when it merges. #88 stays out: it needs the
Workflow to count local and external review rounds separately (workflow#43).

## Implementation

Plan revision 8 is approved (`b4f3a07`). Implementation is complete (CP1-CP5) and technical approval is recorded
(`260b07d`, implementation revision 2).
- **CP1 complete** (`8448016`; #78, #85, #87; docs): the review-figures, 1.7.1 and worker-report sentences match the code.
- **CP2 complete** (`99daccd`; #79, #83, #84, #86, #93, #95; code): `add_figures` keeps a key only the running total
  has, a validation entry reads `(validation)`, the report cut keeps a refusal's named requirement, the
  `_protocol_job_class` docstring names five values, `ci_green` inside another entry's detail no longer starts the CI
  wait (`f413935`, after the first implementation review), and `GhForge(vet_root=...)` sets it at construction.
- **CP3 complete** (`5943de6`; #82, #94; tests): the fake workers' timers are disarmed at exit, which ended the
  `SIGALRM` flake, and the `gh` vetting tests refuse a world-writable executable.
- **CP4 complete** (`1d8b877`; #91): `/satisfy-gate` is routed by stage, `satisfy-gate` (Haiku 5.5 `high`) for plan and
  technical approval and `satisfy-gate-acceptance` (Sonnet 5.5 `medium`) for acceptance; settings table generation 11.
- **CP5 complete** (`d3958da`, `df07c4b`): the roadmap and the fixed follow-ups recorded; the commands guide counts
  the five protocol-only roles.

## Functional review checklist

Findings go to `.ai-review/workflow-controller-cleanup-2/feedback/FUNCTIONAL_REVIEW.md`.

### Setup

- Branch `milestone/workflow-controller-cleanup-2`, clean tree. Run the tests from the checkout; do not set `PYTHONPATH=.` (it
  breaks the decoy test).
- Test data: none to seed. Flows 1-9 use the unit suite and the checkout; flow 10 (real models) needs a scratch
  installation as described in plan decision D5.

### Flows

1. **Documents (#78, #85, #87).** `python3 tools/check_docs.py` passes. Read the `ReviewLaunchRefused` entry in
   `docs/common-problems.md` (figures only for a Claude review with a `result` event), the 1.7.1 note in
   `docs/compatibility.md` (later releases list only `pr.apply_review`), and the worker-report sentence in
   `docs/guide/automation.md` (`gate.what_is_required`, no `last_worker_report`). Expected: each matches the code
   (`controller/review.py`, `controller/cli.py`).
2. **`add_figures` (#79).** `python3 -m unittest tests.test_review_claude`. Expected: pass, including the unequal-key-set
   case.
3. **Validation evidence label (#83).** `python3 -m unittest tests.test_protocol_decision`. Expected: a validation
   entry reads `(validation)`, an `automatic` entry `(automatic)`.
4. **Report cut (#84, #86).** `python3 -m unittest tests.test_protocol_lifecycle`. Expected: a long refusal with a
   trailing `unmet: see above` keeps its named requirement in `last_worker_report` and `gate.what_is_required`;
   `_protocol_job_class` docstring names the fifth element.
5. **`ci_green` token (#93).** `python3 -m unittest tests.test_pr_report`. Expected: `ci_green` as sole or second entry
   waits; the word inside a `pr_fact_current` detail does not.
6. **`vet_root` (#95) and `gh` vetting (#94).** Same module plus `tests.test_forge` / `tests.test_milestone_branch`.
   Expected: `GhForge(vet_root=...)` sets it at construction; a world-writable `gh` executable is refused with
   "world-writable".
7. **SIGALRM flake (#82).** `python3 -m unittest tests.test_observation_equivalence` pinned to two CPUs with several
   parallel copies (`taskset -c 0,1`). Expected: no exit -14; if it still reproduces, the failure now names which
   follower ended and its stderr, and #82 stays open.
8. **Routes (#91).** `python3 -m unittest tests.test_routing tests.test_settings tests.test_protocol_decision`. Expected:
   `satisfy-gate` = `claude-haiku-5-5` `high`, `satisfy-gate-acceptance` = `claude-sonnet-5-5` `medium`, both
   single-agent; `plan.satisfy` and `implementation.satisfy` use the first, `acceptance.satisfy` the second; settings
   table generation 11.
9. **Whole suite and goldens.** Run the full suite and each `tests/golden/generate_*.py --check` (never without
   `--check`). Expected: green except the two known Git 2.56 trailer tests (`test_release_notes`,
   `test_pull_request_lifecycle`).
10. **Real models (D5).** In the isolated scratch installation of plan decision D5: one real Haiku 5.5 `high` plan
    approval and one real Sonnet 5.5 `medium` acceptance, each completing its transaction. Expected: the job records
    show those routes. If Haiku cannot finish the approval, stop for an owner decision.

### Known limitations

- Flow 10 cannot be shown by unit tests and is not exercised by the automated suite.
- #88 is not part of this milestone (waits on workflow#43). #82 stays open if it cannot be reproduced.
- The two Git 2.56 trailer tests fail locally and are unrelated.

## Acceptance

Accepted **by gate policy, not by a person**, on 2026-10-18: `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`). Every checkpoint (CP1-CP5) is `COMPLETE`, the
technical approval `260b07d` (implementation revision 2) is `CURRENT`, flows flow-1 to flow-10 passed, and the
Workflow's own GitHub query found pull request #99 at head `a3a7da7` with every check passing and no standing
objection. The pull request title is `feat:`, so it releases the minor release 1.16.0 when it merges. No
`docs/releases/1.16.0.md` was written by the milestone; the release notes come in the docs pull request after the
release.

## Review history

- **Plan.** Revision 8 was approved by policy at `b4f3a07` after eight local and four Codex plan rounds (Codex
  `REVISE` on revisions 2 and 4, `APPROVE` on revision 8). The findings were about the report-cut rule (D6), the
  commands guide paragraph and the real-model starting state (D5).
- **Implementation.** Technical approval `260b07d` (implementation revision 2) after two local and two Codex rounds.
  Codex round 1 `REVISE` found `ci_green` inside another entry's detail starting the CI wait, fixed in `f413935`
  (#93); round 2 `APPROVE`.
- **Functional.** Round 1: an independent Codex tester passed flows 1-3 and 5-8 (report `functional-tester-round1-RESULTS.md`);
  the operator passed flows 4 and 9 (`functional-review-round1-operator.md`) and flow 10 with real Haiku 5.5 and
  Sonnet 5.5 workers (`functional-flow10-real-models-D5.md`). Checklist evidence commit `a3a7da7`.

## Minor review findings

Eleven Optional review items: eight fixed, two dropped (the pre-change #82 reproduction was not kept) and one kept as
a follow-up, issue #100 (`FAKE_CLAUDE_HANG_CHILD_PID_FILE` is written without an atomic rename). The table with each
reason is `docs/milestones/completed/workflow-controller-cleanup-2-findings.md`.
