# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-cleanup-2` (CU2, `docs/ROADMAP.md`; plan
`docs/ai-workflow/CONTROLLER_CLEANUP_2_PLAN.md`, revision 8) fixes the twelve follow-up issues #78, #79, #82-#87, #91
and #93-#95 in one pull request, the minor release **1.16.0** (`feat:`). Checkpoints CP1-CP5 are `COMPLETE`; technical
approval was recorded by policy at implementation revision 2. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground
truth. The previous milestone, C10b (`workflow-controller-pr-defect-loop`), is released as 1.15.0; its narrative is
`docs/milestones/completed/workflow-controller-pr-defect-loop.md`.

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

## Next action

Test, then put findings in the feedback file and run `/apply-functional-review`, or run `/accept-milestone` if clean.
