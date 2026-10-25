# Active Milestone

## Status

**In progress.** `workflow-controller-review-prompt-rounds` (RB2, issue #161): a round-aware external-review prompt and
the verdict format the Workflow's ingest enforces. Plan revision 2 is approved (policy, `c975470`); the plan is
`docs/ai-workflow/CONTROLLER_REVIEW_PROMPT_ROUNDS_PLAN.md`.

- CP1 (the prompt paragraphs, the changes-file check and the wiring): complete. `controller/review.py` has
  `BUNDLE_TEXT_RULE`, `VERDICT_FORMAT_RULES`, `changes_file_for` and the round paragraph; `_run_review_job` passes
  `changes_path` and `external_round` to `launch_review` and records `review.external_round`. Verified by the four
  review test modules, the golden `--check` and the full suite.
- CP2 (the guides and the roadmap): not started.

## Next action

Run `/milestone-implement workflow-controller-review-prompt-rounds` for CP2.
