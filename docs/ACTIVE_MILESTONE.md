# Active Milestone

## Status

**Implemented, in implementation review.** `workflow-controller-review-prompt-rounds` (RB2, issue #161): a round-aware
external-review prompt and the verdict format the Workflow's ingest enforces. Plan revision 2 is approved (policy,
`c975470`); the plan is `docs/ai-workflow/CONTROLLER_REVIEW_PROMPT_ROUNDS_PLAN.md`. Both checkpoints are complete and the
self-review is done; the full suite passes apart from the two Git 2.56 trailer tests that also fail on `main` locally.

- CP1 (the prompt paragraphs, the changes-file check and the wiring): complete. `controller/review.py` has
  `BUNDLE_TEXT_RULE`, `VERDICT_FORMAT_RULES`, `changes_file_for` and the round paragraph; `_run_review_job` passes
  `changes_path` and `external_round` to `launch_review` and records `review.external_round`. Verified by the four
  review test modules, the golden `--check` and the full suite.
- CP2 (the guides and the roadmap): complete. `docs/run.md`, `docs/guide/workers.md` and `docs/guide/runtime.md` describe the
  fixed round and finding-format text; `docs/ROADMAP.md` marks RB2 implemented and moves the next action to the operator's
  Codex trial. Verified by `tools/check_docs.py`.

## Next action

The implementation review of revision 1: the local review, then the external review, then functional review and
acceptance.
