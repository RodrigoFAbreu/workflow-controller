# Active Milestone

## Status

**In progress.** `workflow-controller-automated-functional-review` (C10c, `docs/ROADMAP.md`) makes the Controller run
the functional review itself at row 38e: an isolated sandbox, an independent tester, one `functional_evidence` result
per checklist flow, and findings for the applier when a flow fails. Plan revision 8 was approved by policy at
`2480ee9`; the base commit is `1599a4d`. The plan is
`docs/ai-workflow/CONTROLLER_AUTOMATED_FUNCTIONAL_REVIEW_PLAN.md`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of the phase and of each checkpoint.

| Checkpoint | State |
| --- | --- |
| CP1 The role, the settings and the pure parts | complete: the `functional-tester` role (Sonnet 5.5, medium, single-agent), the rows `functional.automated` and `functional.timeout_seconds` (table generation 13), and `controller/functional_review.py` (checklist reader and the four flow shapes, `RESULTS.json` validator, "not run" entries, evidence input, findings text, writable-location derivation, prompt). Checked with `tests.test_functional_review`, `tests.test_routing`, `tests.test_settings`, `tests.test_trunk_preflight`, `tests.test_package_structure` and `tools/check_docs.py`; the no-policy golden was regenerated for the two rows. The whole suite (`tools/run_tests.py`, 4414 tests) fails only the two Git 2.56 trailer tests that fail on `main` too. |
| CP2 The enforcement spike on the pinned Claude CLI | pending |
| CP3 The sandbox and the tester run | pending |
| CP4 The step at row 38e | pending |
| CP5 Lifecycle scenarios and the isolation tests | pending |
| CP6 Documentation, roadmap and the whole suite | pending |

## Next action

Run `/milestone-implement workflow-controller-automated-functional-review` for the next checkpoint (one checkpoint per
invocation).
