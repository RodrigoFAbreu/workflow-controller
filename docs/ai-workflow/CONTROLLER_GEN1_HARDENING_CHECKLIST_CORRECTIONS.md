# Controller Gen1 functional-review checklist corrections

This is a real, implementation-time deliverable of work item
`workflow-controller-gen1-correctness-hardening` (CP4), not prose inside a
future checklist. It corrects four checklist-accuracy issues found and
confirmed during Gen1's own functional-review acceptance round
(`.ai-review/feedback/FUNCTIONAL_REVIEW.md`,
`.ai-review/advisory/FUNCTIONAL_REVIEW_pre_round5.md`) — in every case the
Controller's actual, tested, documented behavior was already correct; only
the checklist's own text was wrong. A future functional-review checklist
for this or a related work item links this file rather than re-deriving
its content (`/prepare-functional-review` step 3 explicitly permits linking
to "a short file from `docs/ACTIVE_MILESTONE.md`").

## 1. `--json` placement

`--json` is a global, top-level-only option and must **precede** the
subcommand: `--json inspect .`, `--json explain .`. Placing the flag
after the subcommand instead is not a valid invocation. This is already
correct in `README.md` and in `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`;
only a prior checklist's own examples had the flag in the wrong position.

Correct examples:

- `python3 -m controller --json inspect .`
- `python3 -m controller --json explain .`

## 2. `explain`'s exit code

`explain` exits `0` (`EXIT_OK`) in every branch — `controller/cli.py`'s
`cmd_explain` unconditionally returns `EXIT_OK`. Exit code `10`
(`EXIT_GATE`) is never produced by `explain`; it is only ever produced by
`job.STATUS_GATE_BLOCKED`, reachable from `step`/`run` alone.
`tests/test_cli.py::ExplainCommandTest` already asserts `explain`'s exit
code is `EXIT_OK` in both the automatic-phase and gate-phase cases.

## 3. `--json inspect`'s payload description

`--json inspect`'s payload wraps a `repository` object
(`root`/`workflow_version`/`profile`) alongside a `work_item` object built
by `controller/cli.py`'s `_work_item_payload` (twelve fields:
`work_item_id`, `work_item_type`, `work_item_kind`,
`governing_workflow_version`, `phase`, `plan_revision`,
`implementation_revision`, `functional_review_round`,
`current_checkpoint_id`, `last_completed_checkpoint_id`,
`registry_complete`, `incomplete_children`).

The checkable claim is the negative one: the payload carries **none** of
`plan_approval`, `technical_approval`, `functional_acceptance_status`, or
`blocking_decisions`. An exhaustive positive enumeration of everything the
payload *does* carry is the wrong shape for this checklist and is not
restated here.

## 4. Flow 3 (`explain`, the functional-review gate) precondition check

Before assuming the "checklist current, no findings yet" branch at
`AWAITING_FUNCTIONAL_REVIEW`, a tester must first check whether
`.ai-review/feedback/FUNCTIONAL_REVIEW.md` already exists, unconsumed,
from a prior round. `controller/evidence.py`'s `AWAITING_FUNCTIONAL_REVIEW`
handling already has a distinct, tested, automatic sub-case for exactly
this: an unconsumed `FUNCTIONAL_REVIEW.md` routes automatically to
`/apply-functional-review`, never to a fresh manual checklist pass. This
sub-case's own regression coverage is
`tests/test_evidence.py::AwaitingFunctionalReviewTest::test_unconsumed_findings_are_automatic`.
The code is correct; a prior checklist simply never told a tester to check
for this precondition, leaving the tester to improvise it.
