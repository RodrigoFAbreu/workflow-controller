# C10c: the automated functional review (`workflow-controller-automated-functional-review`)

This is the record of milestone `workflow-controller-automated-functional-review` (`docs/ROADMAP.md` step C10c),
archived when it was accepted on 2026-10-21. Plan: `docs/ai-workflow/CONTROLLER_AUTOMATED_FUNCTIONAL_REVIEW_PLAN.md`,
revision 8, approved by policy at `2480ee9`; base commit `1599a4d`. The checklist below is the active-milestone page as
it stood during the functional review; the later sections were added at acceptance.

## Functional review checklist

**Setup.** Work in a fresh clone of the milestone branch at the committed head. Install nothing global; run the
Controller as `PYTHONPATH=<clone> python3 -P -m controller`. `bubblewrap` (`bwrap`) and `socat` must be on `PATH`
and a pinned `claude` CLI must be installed. Every flow below runs offline against throwaway repositories under a
`mktemp -d` directory; none needs a network, a real remote or a real `gh`.

**Test data.** A scratch repository managed by the vendored Workflow with a work item at the functional-review gate
and a short committed checklist of two flows, one that passes and one that fails (for example a command that exits
non-zero).

**A. The unit tests for the feature pass.** Run
`python3 -m unittest tests.test_functional_review tests.test_functional_sandbox tests.test_functional_lifecycle tests.test_protocol_job`
from the clone root. Expect `OK` with nothing skipped for the sandbox tests when `bwrap` and `socat` are present.

**B. The checklist is read as flows.** Run `python3 -P -m controller explain` in the scratch repository at the gate.
Expect exit 0 and a forecast that names the head, the checklist blob, the flow ids (`flow-1`, `flow-2`), the previous
job and any kept sandbox (no timeout or tester identity: plan D9), and no job, sandbox or reservation created.

**C. A passing run is recorded.** With a checklist whose flows all pass, run one step at row 38e. Expect one
`functional_evidence` result per flow and a tester job record of kind `functional_test` that is `FINISHED` with
outcome `recorded`. Acceptance by policy follows on a later step (the Workflow's rows 38f-38h), not on this one.

**D. A failing flow produces findings.** With the failing flow, run one step. Expect `FUNCTIONAL_REVIEW.md` in the
feedback folder, headed with the tested head, checklist blob and run, quoting the failing flow's log; the next step
goes to `/apply-functional-review`, not to acceptance.

**E. The sandbox refuses writes.** Read `RESULTS.json` and the fake `gh`/`git push` logs of the run in C or D.
Expect the clone's tracked files, the checklist and `.git` byte-identical to the tested head, and the fake remote
calls recorded, not performed.

**F. Settings switch it off.** Set `functional.automated` to `false` in the settings file and run a step. Expect a
stop for the manual gate with no tester job, sandbox or usage reservation.

**G. No sandbox tools means a clean refusal.** Run a step with `bwrap` hidden from `PATH`. Expect the stop
`functional_tester_unavailable` before any model call, and no job left `LAUNCHED`.

**H. An unreadable checklist stops early.** Replace the checklist section with prose that has no flows. Expect
`checklist_unrecognised` naming the four accepted flow shapes, before any tester job, sandbox or reservation exists
(the ordinary stop record is still written, as at every stop).

**I. The live tester runs at the default runtime layout.** In the scratch worlds of C and D with the verified native
`claude` build and a runtime folder at the default depth (not a short `/tmp` path), run one step. Expect the tester's
Bash calls to run, `RESULTS.json` written, and the same outcomes as C and D; during the run the tester's `TMPDIR` is a
`wcf-*` folder (mode 0700) under `$XDG_RUNTIME_DIR` or `/tmp`, removed with the sandbox. With the tester's message
unusable (for example `RESULTS.json` removed by a stub), the `results_invalid` stop text quotes the tester's final
message.

**Known limitations.** Pull-request flows are unsupported in the sandbox; a copy edited under the tester's own
`TMPDIR` is not detected (stated limit of D5); a Codex tester, `required_flows` coverage tooling, a tester-failure
cap setting and a sandbox-side forge model are roadmap follow-ups 61-66. Two Git 2.56 trailer tests fail on `main`
too and are out of scope. Out of scope: the real forge, releases and the installed pipx copy.

## Acceptance

Accepted **by gate policy, not by a person**, on 2026-10-21: `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`). Every checkpoint (CP1-CP6) is `COMPLETE`, the
technical approval `6eb86c3` (implementation revision 11) is `CURRENT`, flows flow-1 to flow-9 passed, and the
Workflow's own GitHub query found pull request #111 at head `df1acdd` with every check passing and no standing
objection. The pull request title is `feat:`, so it releases the minor release 1.18.0 when it merges. The release
notes come in the docs pull request after the release.

## Review history

- **Plan.** Revision 8 was approved by policy at `2480ee9`.
- **Implementation.** Eleven implementation revisions; technical approval `6eb86c3` (implementation revision 11) was
  recorded by policy.
- **Functional.** Round 1 found the tester's Claude sandbox could not start at the default runtime layout and that a
  missing `RESULTS.json` hid the tester's reason; both were fixed in a bounded round. Round 2: an independent Codex
  tester and an operator live run with the verified `claude` 2.1.303 passed all nine flows. Checklist evidence
  commit `df1acdd`.

## Minor review findings

Sixty-one Optional review items: thirty-one fixed, fifteen kept as follow-ups (issues #114 to #118) and fifteen
dropped with a reason. The table with each reason is
`docs/milestones/completed/workflow-controller-automated-functional-review-findings.md`.
