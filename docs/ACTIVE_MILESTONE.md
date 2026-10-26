# Active Milestone

## Status

**In progress.** `workflow-controller-checks-wait` (NF2, [#170](https://github.com/RodrigoFAbreu/workflow-controller/issues/170)),
plan revision 3 (`docs/ai-workflow/CONTROLLER_CHECKS_WAIT_PLAN.md`): a spent pending-checks wait, or a pending-checks stop
in a repository without auto-merge, sends the info event `checks_wait` (INFO, LOW), and `lane_blocked` only once the checks
have been pending over four hours.

- CP1 (`checks_wait` and the long escalation): complete. `controller/notify.py` and `controller/job.py` carry the event and
  the age rule on both paths; the new and moved tests pass, and the full suite is green but for the known Git 2.56 trailer
  tests and the scenario tests that already fail at the base commit.
- CP2 (the guides and the roadmap): not started.

## Next action

Run `/milestone-implement workflow-controller-checks-wait` for CP2.
