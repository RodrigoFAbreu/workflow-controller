# Active Milestone

## Status

**In progress.** `workflow-controller-round-caps` (C13b, `docs/ROADMAP.md`; controller issue
[#88](https://github.com/RodrigoFAbreu/workflow-controller/issues/88)) caps local and external review rounds
separately: local 6, external 4. Plan revision 3 was approved by policy at `513c873`; the base commit is `d6b9dfc`.
The plan is `docs/ai-workflow/CONTROLLER_ROUND_CAPS_PLAN.md`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of the phase and of each checkpoint.

| Checkpoint | State |
| --- | --- |
| CP1 The two settings and the options | complete: `review.local_round_cap` and `review.external_round_cap` (table generation 12), `--review-local-round-cap` and `--review-external-round-cap` on `run` and `step`; the no-policy golden regenerated for the two rows. Checked with `tests.test_settings`, `tests.test_trunk_preflight`, `tests.test_cli`, `tests.test_docs` and `tools/check_docs.py`. |
| CP2 The cap rule over split counts | pending |
| CP3 The gate reads the rule | pending |
| CP4 Documentation, roadmap and the whole suite | pending |

## Next action

Run `/milestone-implement workflow-controller-round-caps` for the next checkpoint (one checkpoint per invocation).
